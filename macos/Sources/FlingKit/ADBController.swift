// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: "ADB in one click" (Windows / Linux 0.5.4) for the Mac panel — the
/// TV's ADB ports from mDNS, its own remembered port, "Allow" on the TV, the
/// 6-digit pairing code, IP and ports by hand as a fallback — plus what the
/// TV really plays, a screenshot and installing an APK.
///
/// All adb calls are blocking subprocesses and run off the main actor.
@MainActor
public final class ADBController: ObservableObject {

    public enum Link: Equatable {
        /// Not tried yet for this TV.
        case unknown
        /// adb isn't on this Mac.
        case missing
        case working
        case connected(port: Int)
        /// The TV shows "Allow debugging?" — Fling connects once it's pressed.
        case waitingAllow
        /// The TV shows "Pair with code" — six digits needed.
        case needsCode
        /// No ADB port answers: debugging is off. Fling connects once it's on.
        case debuggingOff
        case failed(String)
    }

    @Published public private(set) var link: Link = .unknown
    @Published public private(set) var nowPlaying: ADBNowPlaying = .idle
    /// A plain-words result of the last action (error or done).
    @Published public private(set) var message: String?
    @Published public private(set) var messageIsError = false
    @Published public private(set) var busy = false
    /// The TV this controller works with (the panel's selected TV, or one typed by hand).
    @Published public private(set) var ip: String?

    public var isConnected: Bool { if case .connected = link { return true }; return false }

    /// Set by the app: is the panel open? (Polls faster then.)
    public var panelOpen = false { didSet { if panelOpen && !oldValue { pollSoon() } } }
    /// Set by the app: is a Cast session on? (Polls every 10 s then, panel closed.)
    public var somethingCasting: () -> Bool = { false }
    /// Optional artwork/duration lookup for a title (YouTube search in the app).
    public var artworkLookup: (@Sendable (String, String?) async -> (URL, TimeInterval?)?)?

    private let locate: () -> String?
    private let makeClient: (String) -> ADBClient
    private var client: ADBClient?
    private var watchTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var polling = false
    private var lastPoll = Date.distantPast
    /// Per-TV quiet period after a refusal, so polling never re-prompts the TV.
    private var backoff: [String: Date] = [:]
    private var artCache: [String: (URL, TimeInterval?)] = [:]

    public init(locate: @escaping () -> String? = { ADBLocator.find() },
                makeClient: @escaping (String) -> ADBClient = { ADBClient(executable: $0) }) {
        self.locate = locate
        self.makeClient = makeClient
    }

    /// adb, found again on each call until it is there (installing it needs no restart).
    private func adb() -> ADBClient? {
        if let client { return client }
        guard let path = locate() else { return nil }
        let c = makeClient(path)
        client = c
        return c
    }

    public var adbInstalled: Bool { adb() != nil }

    // MARK: - which TV

    /// The panel's TV changed: forget the other TV's link state.
    public func use(ip newIP: String?) {
        guard newIP != ip else { return }
        ip = newIP
        stopWatch()
        nowPlaying = .idle
        message = nil
        link = .unknown
        // A TV that worked before reconnects quietly (no prompt: it already trusts this Mac).
        if let newIP, let c = adb(), c.savedPort(newIP) != nil { pollSoon() }
    }

    // MARK: - connect in one click

    /// One button: the selected TV, the port it announces (or the one that
    /// worked), and then whatever the TV needs.
    public func connect(ip typedIP: String? = nil, port typedPort: Int? = nil) async {
        if let typedIP, !typedIP.isEmpty {
            guard ADBParse.isIPv4(typedIP) else {
                fail("The TV's IP is four numbers with dots, e.g. 192.168.1.50."); return
            }
            if typedIP != ip { use(ip: typedIP) }
        }
        guard let ip else { fail("Choose a TV first (or type its IP by hand)."); return }
        guard let c = adb() else { link = .missing; return }
        stopWatch()
        backoff[ip] = nil
        link = .working
        message = nil
        let (reach, ann) = await Task.detached(priority: .userInitiated) { () -> (ADBReach, ADBAnnounce) in
            let ann = c.mdns()[ip] ?? ADBAnnounce()
            return (c.reach(ip: ip, typed: typedPort, announce: ann), ann)
        }.value
        guard ip == self.ip else { return }
        after(reach, announce: ann, ip: ip)
    }

    private func after(_ reach: ADBReach, announce: ADBAnnounce, ip: String) {
        switch reach {
        case .device(let port):
            connected(port: port)
        case .unauthorized:
            link = .waitingAllow
            startWatch(.allow, seconds: 120, ip: ip)
        case .offline, .closed:
            if announce.pairing != nil {
                link = .needsCode
            } else {
                link = .debuggingOff
                startWatch(.enable, seconds: 300, ip: ip)
            }
        }
    }

    private func connected(port: Int) {
        stopWatch()
        link = .connected(port: port)
        if let ip { backoff[ip] = nil }
        pollSoon()
    }

    /// The 6 digits from "Pair with code". `pairingPort` only when typed by hand.
    public func pair(code raw: String, pairingPort: Int? = nil) async {
        let code = raw.filter(\.isNumber)
        guard code.count == 6 else { fail("The pairing code is the 6 digits on the TV screen."); return }
        guard let ip else { fail("Choose a TV first (or type its IP by hand)."); return }
        guard let c = adb() else { link = .missing; return }
        stopWatch()
        link = .working
        message = nil
        let result = await Task.detached(priority: .userInitiated) { () -> (ok: Bool, output: String)? in
            guard let port = pairingPort ?? c.mdns()[ip]?.pairing else { return nil }
            return c.pair(ip: ip, port: port, code: code)
        }.value
        guard ip == self.ip else { return }
        guard let result else {
            link = .needsCode
            fail("The code window on the TV has closed. On the TV: Wireless debugging → Pair device with pairing code, then type the new code.")
            return
        }
        guard result.ok else {
            link = .needsCode
            fail("Didn't work: \(ADBParse.lastLine(result.output) ?? result.output)")
            return
        }
        // The connect port shows up on mDNS right after pairing.
        try? await Task.sleep(for: .milliseconds(1200))
        await connect()
    }

    public func disconnect() async {
        guard let ip, let c = adb() else { return }
        stopWatch()
        await Task.detached { c.disconnect(ip: ip) }.value
        backoff[ip] = Date().addingTimeInterval(30)
        link = .unknown
        nowPlaying = .idle
        done("ADB disconnected")
    }

    // MARK: - waiting for the user at the TV

    enum WatchMode { case allow, enable }

    private func startWatch(_ mode: WatchMode, seconds: TimeInterval, ip: String) {
        stopWatch()
        let until = Date().addingTimeInterval(seconds)
        watchTask = Task { [weak self] in
            while Date() < until {
                try? await Task.sleep(for: .milliseconds(2500))
                guard !Task.isCancelled, let self, self.ip == ip, let c = self.adb() else { return }
                switch mode {
                case .allow:
                    let st = await Task.detached { c.stateNow(ip: ip) }.value
                    guard !Task.isCancelled, self.ip == ip else { return }
                    if let st, st.state == "device" {
                        let port = st.port ?? c.savedPort(ip) ?? 5555
                        c.remember(port: port, for: ip)
                        self.connected(port: port)
                        return
                    }
                case .enable:
                    let ann = await Task.detached { c.mdns()[ip] }.value
                    guard !Task.isCancelled, self.ip == ip else { return }
                    if ann?.pairing != nil { self.link = .needsCode; self.watchTask = nil; return }
                    if ann?.connect != nil || ann?.adb != nil {
                        self.watchTask = nil
                        await self.connect()
                        return
                    }
                }
            }
            // Gave up waiting; the panel still offers the button.
            guard let self, !Task.isCancelled, self.ip == ip else { return }
            if mode == .allow { self.backoff[ip] = Date().addingTimeInterval(300) }
            self.watchTask = nil
        }
    }

    private func stopWatch() {
        watchTask?.cancel()
        watchTask = nil
    }

    // MARK: - what the TV plays

    /// Every 2 s tick from the app; decides itself whether it's time:
    /// panel open → every 3 s; closed and something plays → 10 s; idle → once a minute.
    public func tick(now: Date = Date()) {
        let live = nowPlaying.isLive || somethingCasting()
        let every: TimeInterval = panelOpen ? 3 : (live ? 10 : 60)
        guard now.timeIntervalSince(lastPoll) >= every else { return }
        pollSoon()
    }

    private func pollSoon() {
        guard !polling, let ip, let c = adb() else { return }
        // Only for a TV that already worked: polling a TV that never allowed
        // this Mac would put "Allow debugging?" on its screen.
        guard isConnected || c.savedPort(ip) != nil else { return }
        if let until = backoff[ip], until > Date() { return }
        if case .waitingAllow = link { return }
        polling = true
        lastPoll = Date()
        let wasConnected = isConnected
        pollTask = Task { [weak self] in
            let result = await Task.detached(priority: .utility) { () -> (ADBNowPlaying?, String?) in
                if let np = c.nowPlaying(ip: ip) { return (np, nil) }
                // One quiet reconnect; a refusal stops polling for a while.
                let st = c.reconnect(ip: ip)
                if st == "device", let np = c.nowPlaying(ip: ip) { return (np, nil) }
                return (nil, st)
            }.value
            guard let self else { return }
            self.polling = false
            guard self.ip == ip else { return }
            let (np, st) = result
            if var np {
                if !self.isConnected { self.link = .connected(port: c.savedPort(ip) ?? 5555) }
                if let title = np.title, np.isLive {
                    let key = "\(title)|\(np.artist ?? "")"
                    if let hit = self.artCache[key] {
                        np.artwork = hit.0; np.duration = hit.1
                    } else if let lookup = self.artworkLookup {
                        Task { [weak self] in
                            let hit = await lookup(title, np.artist)
                            guard let self, let hit else { return }
                            self.artCache[key] = hit
                            if self.nowPlaying.title == title {
                                self.nowPlaying.artwork = hit.0; self.nowPlaying.duration = hit.1
                            }
                        }
                    }
                }
                self.nowPlaying = np
            } else {
                self.nowPlaying = .idle
                self.backoff[ip] = Date().addingTimeInterval(st == "unauthorized" ? 300 : 30)
                if wasConnected || st == "unauthorized" {
                    self.link = st == "unauthorized" ? .waitingAllow : .unknown
                }
            }
        }
    }

    /// For tests: waits for the poll in flight.
    public func settle() async {
        await pollTask?.value
    }

    // MARK: - screenshot / APK

    /// Screenshot to `folder` (~/Pictures/Fling in the app). Returns the file.
    public func screenshot(to folder: URL) async -> URL? {
        guard let (c, ip) = ready("Screenshot") else { return nil }
        busy = true
        defer { busy = false }
        let r = await Task.detached(priority: .userInitiated) { () -> Result<(URL, Bool), Error> in
            Result { let u = try c.screenshot(ip: ip, folder: folder); return (u, ADBClient.looksBlack(u)) }
        }.value
        switch r {
        case .success(let (url, black)):
            if black {
                fail("The screenshot is black: the video is protected from capture (DRM in YouTube / Netflix). Menus and the home screen capture fine.")
            } else {
                done("TV screenshot saved: \(url.path)")
            }
            return url
        case .failure(let e):
            if case ADBClient.ShotError.failed(let why) = e { fail("The screenshot didn't work: \(why)") }
            else { fail("The screenshot didn't work: \(e.localizedDescription)") }
            return nil
        }
    }

    public func installAPK(_ files: [URL]) async {
        let apks = files.filter { $0.pathExtension.lowercased() == "apk" }.map(\.path)
        guard !apks.isEmpty else { fail("That's not an APK file."); return }
        // Opened from Finder right at launch: the TV may still be on its way.
        for _ in 0..<30 where ip == nil { try? await Task.sleep(for: .milliseconds(500)) }
        // The user asked for it: one connect is fine (a TV that trusts this Mac doesn't ask).
        if !isConnected, ip != nil, adb() != nil { await connect() }
        guard let (c, ip) = ready("APK install") else { return }
        let name = (apks[0] as NSString).lastPathComponent
        busy = true
        defer { busy = false }
        done("Installing on the TV: \(name)…")
        let why = await Task.detached(priority: .userInitiated) { c.install(ip: ip, apks: apks) }.value
        if let why { fail("APK install failed: \(why)") } else { done("Installed on the TV: \(name)") }
    }

    /// The client and TV when ADB is connected, else a plain-words reason.
    private func ready(_ what: String) -> (ADBClient, String)? {
        guard let ip else { fail("No TV chosen — click Choose at the bottom of the panel."); return nil }
        guard let c = adb() else { link = .missing; fail("\(what): adb isn't installed on this Mac."); return nil }
        guard isConnected else { fail("\(what) works over ADB — connect ADB first."); return nil }
        return (c, ip)
    }

    /// Quit: stops Fling's own adb server only (Android Studio's is left alone).
    public func shutdown() {
        stopWatch()
        client?.stopOwnServer()
    }

    private func fail(_ text: String) { message = text; messageIsError = true }
    private func done(_ text: String) { message = text; messageIsError = false }
}
