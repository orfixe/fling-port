// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import SwiftUI
import Combine
import UniformTypeIdentifiers
import FlingKit

/// Port: wires the ADB controller (FlingKit/ADBController.swift) into the app —
/// the selected TV, polling, the panel being open, artwork, quitting.
@MainActor
enum ADBGlue {
    static let shared = ADBController()
    private static var cancellables = Set<AnyCancellable>()
    private static var timer: Timer?

    static func install(state: AppState) {
        let adb = shared
        adb.somethingCasting = { [weak state] in state?.panel == .casting }
        adb.artworkLookup = { title, artist in await youtubeLookup(title: title, artist: artist) }
        state.$selectedDevice
            .map { $0?.ip }
            .removeDuplicates()
            .receive(on: RunLoop.main)
            .sink { adb.use(ip: $0) }
            .store(in: &cancellables)
        // The panel is the app's only popover.
        let nc = NotificationCenter.default
        nc.publisher(for: NSPopover.didShowNotification)
            .sink { _ in adb.panelOpen = true }.store(in: &cancellables)
        nc.publisher(for: NSPopover.didCloseNotification)
            .sink { _ in adb.panelOpen = false }.store(in: &cancellables)
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in
            Task { @MainActor in shared.tick() }
        }
    }

    static func shutdown() {
        timer?.invalidate()
        shared.shutdown()
    }

    static var screenshotFolder: URL {
        FileManager.default.urls(for: .picturesDirectory, in: .userDomainMask).first!
            .appendingPathComponent("Fling", isDirectory: true)
    }

    static func screenshot() {
        Task {
            if let url = await shared.screenshot(to: screenshotFolder) {
                NSWorkspace.shared.open(url)
            }
        }
    }

    /// File picker; several files at once are the parts of one split app.
    static func chooseAPK() {
        let panel = NSOpenPanel()
        panel.title = tr("Install an APK on the TV")
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        if let apk = UTType(filenameExtension: "apk") { panel.allowedContentTypes = [apk] }
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK else { return }
        install(panel.urls)
    }

    /// From the picker, a drop on the panel, or Finder's "Open With".
    static func install(_ urls: [URL]) {
        Task { await shared.installAPK(urls) }
    }

    /// Best-effort artwork + duration for a title: the first YouTube search hit
    /// (same as the Windows build).
    nonisolated static func youtubeLookup(title: String, artist: String?) async -> (URL, TimeInterval?)? {
        let q = "\(title) \(artist ?? "")".trimmingCharacters(in: .whitespaces)
        var c = URLComponents(string: "https://www.youtube.com/results")!
        c.queryItems = [URLQueryItem(name: "search_query", value: q)]
        guard let url = c.url else { return nil }
        var req = URLRequest(url: url, timeoutInterval: 6)
        req.setValue("Mozilla/5.0", forHTTPHeaderField: "User-Agent")
        req.setValue("en", forHTTPHeaderField: "Accept-Language")
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let html = String(data: data, encoding: .utf8),
              let hit = ADBParse.youtubeSearch(html) else { return nil }
        return (hit.image, hit.duration)
    }
}

/// The ADB block of the panel: status, what the TV needs next, the code, "by
/// hand", and once connected — what plays, screenshot, APK.
struct ADBSection: View {
    @ObservedObject var adb: ADBController
    @ObservedObject var state: AppState
    @State private var code = ""
    @State private var manual = false
    @State private var manualIP = ""
    @State private var manualPort = ""
    @State private var manualPairPort = ""
    @State private var dropping = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if adb.nowPlaying.isLive { ADBNowPlayingCard(np: adb.nowPlaying) }
            HStack(spacing: 7) {
                SectionLabel(text: "ADB")
                Circle().fill(dotColor).frame(width: 6, height: 6)
                Text(tr(statusText)).font(.system(size: 11)).foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                if adb.link == .working || adb.busy {
                    ProgressView().controlSize(.small)
                } else if !adb.isConnected {
                    Button(tr(adb.link == .unknown ? "Connect" : "Try again")) { connect() }
                        .font(.system(size: 11))
                }
            }
            .padding(.horizontal, 14).padding(.top, 7).padding(.bottom, 3)

            if let hint {
                Text(tr(hint))
                    .font(.system(size: 10.5)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.bottom, 4)
            }
            if adb.link == .missing {
                HStack(spacing: 6) {
                    Text(ADBLocator.installCommand)
                        .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    Button(tr("Copy")) {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(ADBLocator.installCommand, forType: .string)
                    }
                    .font(.system(size: 11))
                }
                .padding(.horizontal, 14).padding(.bottom, 4)
            }
            if adb.link == .needsCode { codeEntry }

            if adb.isConnected {
                MenuRow(title: "TV screenshot", icon: "camera", enabled: !adb.busy) { ADBGlue.screenshot() }
                MenuRow(title: "Install APK on the TV…", icon: "shippingbox", enabled: !adb.busy) {
                    ADBGlue.chooseAPK()
                }
                MenuRow(title: "Disconnect ADB", icon: "xmark.circle") { Task { await adb.disconnect() } }
            } else if adb.link != .missing {
                MenuRow(title: manual ? "Hide the by-hand fields" : "By hand: IP and port…") {
                    manual.toggle()
                    if manual, manualIP.isEmpty { manualIP = adb.ip ?? state.selectedDevice?.ip ?? "" }
                }
                if manual { manualFields }
            }

            if let msg = adb.message {
                Text(tr(msg))
                    .font(.system(size: 10.5)).foregroundStyle(adb.messageIsError ? .red : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14).padding(.vertical, 3)
            }
        }
        .padding(.bottom, 4)
        .background(dropping ? Color.accentColor.opacity(0.12) : Color.clear)
        // Drop an APK on the ADB block to install it.
        .onDrop(of: [.fileURL], isTargeted: $dropping) { providers in
            let group = DispatchGroup()
            let urls = URLBox()
            for p in providers {
                group.enter()
                _ = p.loadObject(ofClass: URL.self) { url, _ in
                    if let url { urls.append(url) }
                    group.leave()
                }
            }
            group.notify(queue: .main) { ADBGlue.install(urls.all) }
            return true
        }
    }

    private var codeEntry: some View {
        HStack(spacing: 6) {
            TextField(tr("6 digits from the TV"), text: $code)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
                .onSubmit { pair() }
                .onChange(of: code) { _, v in
                    let c = String(v.filter(\.isNumber).prefix(6)); if c != v { code = c }
                }
            Button(tr("Pair")) { pair() }
                .font(.system(size: 11.5)).disabled(code.count != 6)
        }
        .padding(.horizontal, 14).padding(.bottom, 5)
    }

    private var manualFields: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(tr("IP:")).font(.system(size: 11)).foregroundStyle(.secondary)
                TextField("192.168.1.50", text: $manualIP)
                    .textFieldStyle(.roundedBorder).font(.system(size: 11.5, design: .monospaced))
                    .onSubmit { connect() }
            }
            HStack(spacing: 6) {
                Text(tr("Port:")).font(.system(size: 11)).foregroundStyle(.secondary)
                TextField("5555", text: $manualPort)
                    .textFieldStyle(.roundedBorder).font(.system(size: 11.5, design: .monospaced))
                    .onSubmit { connect() }
                Text(tr("pairing:")).font(.system(size: 11)).foregroundStyle(.secondary)
                TextField(tr("port"), text: $manualPairPort)
                    .textFieldStyle(.roundedBorder).font(.system(size: 11.5, design: .monospaced))
                    .frame(width: 64)
            }
            Text(tr("Usually not needed: Fling finds the ports itself. On the TV: Wireless debugging → IP address & Port."))
                .font(.system(size: 10)).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(tr("Connect")) { connect() }.font(.system(size: 11))
            }
        }
        .padding(.horizontal, 14).padding(.bottom, 5)
    }

    private func connect() {
        // "IP:port" pasted in either field is split up.
        var ip = manualIP.trimmingCharacters(in: .whitespaces)
        var port = Int(manualPort.trimmingCharacters(in: .whitespaces))
        for text in [manualIP, manualPort] {
            let t = text.trimmingCharacters(in: .whitespaces)
            if let colon = t.lastIndex(of: ":"), let p = Int(t[t.index(after: colon)...]) {
                ip = String(t[..<colon]); port = p
            }
        }
        Task { await adb.connect(ip: manual ? ip : nil, port: manual ? port : nil) }
    }

    private func pair() {
        guard code.count == 6 else { return }
        let c = code
        code = ""
        Task { await adb.pair(code: c, pairingPort: manual ? Int(manualPairPort) : nil) }
    }

    private var dotColor: Color {
        switch adb.link {
        case .connected: return .green
        case .waitingAllow, .needsCode, .debuggingOff, .working: return .orange
        case .missing, .failed: return .red
        case .unknown: return .secondary
        }
    }

    private var statusText: String {
        switch adb.link {
        case .unknown: return adb.ip == nil ? "No TV chosen" : "Not connected"
        case .missing: return "adb isn't installed"
        case .working: return "Connecting…"
        case .connected(let port): return "Connected (port \(port))"
        case .waitingAllow: return "Waiting for “Allow” on the TV…"
        case .needsCode: return "The TV asks for a code"
        case .debuggingOff: return "Debugging is off on the TV"
        case .failed: return "Didn't work"
        }
    }

    private var hint: String? {
        switch adb.link {
        case .unknown where adb.ip != nil:
            return "ADB shows what really plays on the TV, takes screenshots and installs apps."
        case .missing:
            return "Fling uses adb from Android platform-tools. Install it in Terminal, then press Try again:"
        case .waitingAllow:
            return "Look at the TV screen: press “Allow” and tick “Always allow from this computer”. Fling connects by itself as soon as you do."
        case .needsCode:
            return "The TV shows pairing — type the 6 digits from the TV screen and press Pair."
        case .debuggingOff:
            return "Turn on debugging on the TV — Fling notices by itself:\n1. Settings → About → press “Build” 7 times.\n2. Settings → Developer options → “Network debugging” (or “USB debugging”).\nAndroid 11+: “Wireless debugging” → “Pair device with pairing code” — the code shows on the TV, type it here."
        default:
            return nil
        }
    }
}

private final class URLBox: @unchecked Sendable {
    private let lock = NSLock()
    private var urls: [URL] = []
    func append(_ u: URL) { lock.lock(); urls.append(u); lock.unlock() }
    var all: [URL] { lock.lock(); defer { lock.unlock() }; return urls }
}

/// What the TV really plays (its media session over ADB).
struct ADBNowPlayingCard: View {
    let np: ADBNowPlaying

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Artwork(url: np.artwork, height: 48)
                .frame(width: 84)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            VStack(alignment: .leading, spacing: 2) {
                Text(tr(np.title ?? "Playing"))
                    .font(.system(size: 12, weight: .semibold)).lineLimit(2)
                if let artist = np.artist {
                    Text(tr(artist)).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    HStack(spacing: 4) {
                        Text(tr(stateLabel))
                        if let p = np.position(now: ctx.date) {
                            Text(tr("·"))
                            Text(tr(clock(p) + (np.duration.map { " / " + clock($0) } ?? "")))
                                .monospacedDigit()
                        }
                    }
                    .font(.system(size: 10.5)).foregroundStyle(.tertiary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.top, 8).padding(.bottom, 2)
        .help(tr(np.package.map { "On the TV: \($0)" } ?? "On the TV"))
    }

    private var stateLabel: String {
        switch np.state {
        case .playing: return "Playing on the TV"
        case .paused: return "Paused on the TV"
        case .buffering: return "Loading on the TV…"
        case .idle: return ""
        }
    }

    private func clock(_ t: TimeInterval) -> String {
        let total = Int(t.rounded())
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
    }
}
