// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import Combine
import FlingKit

/// Port: what the Windows / Linux build adds around casting, wired to upstream's AppState
/// from one place — the browser-extension bridge, the ⌃⌥⌘C hotkey, the file queue,
/// "now on TV" notifications and the Settings window.
@MainActor
final class PortServices: ObservableObject {
    static let shared = PortServices()

    let settings = PortSettings.shared
    let queue = MediaQueue()
    private(set) var state: AppState?
    private var bridge: BrowserBridge?
    private var hotkey: GlobalHotkey?
    private var cancellables = Set<AnyCancellable>()
    private var pollTimer: Timer?
    private var detector = TrackChangeDetector()
    /// A cast started outside the panel: poll the TV a while so the menu bar and
    /// notifications learn about it.
    private var watchUntil = Date.distantPast
    /// The bridge's cast is queued but AppState hasn't flagged it in flight yet.
    private var bridgeCasting = false

    /// Set by StatusItemController.
    var isPanelShown: () -> Bool = { false }
    var openPanel: () -> Void = {}

    /// The active tab of the browser the extension last reported (Firefox, Edge… too).
    @Published private(set) var browserTab: BrowserTab?
    @Published private(set) var bridgeError: String?

    struct BrowserTab: Equatable {
        let url: String
        let title: String
        let browser: String
        /// Where the page's player is, worth resuming at (the extension's resumePoint).
        let resumeAt: TimeInterval?
        let mediaSrc: String?
        let at: Date
        var fresh: Bool { Date().timeIntervalSince(at) < 180 }   // the extension's heartbeat is 1 min
    }

    func start(state: AppState) {
        self.state = state
        queue.executable = CattClient.resolveExecutable()
        queue.device = { [weak state] in state?.selectedDevice?.ip }
        queue.language = Lang.current
        queue.tr = { tr($0) }
        queue.notify = { Notifier.shared.post($0) }
        queue.error = { [weak state] in state?.lastError = $0 }
        queue.pollSoon = { [weak self] in self?.watch(seconds: 30) }
        Task.detached(priority: .background) { MediaFiles.cleanTmp() }

        state.$status
            .receive(on: RunLoop.main)
            .sink { [weak self] st in self?.statusChanged(st) }
            .store(in: &cancellables)

        if settings.bridge { startBridge() }
        if settings.hotkey { setHotkey(true) }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.tick() }
        }
    }

    // MARK: - status, notifications

    private func statusChanged(_ st: CastStatus) {
        guard let state else { return }
        queue.observe(st)
        guard let title = detector.feed(device: state.selectedDevice?.ip, status: st) else { return }
        guard settings.notifyTrack, !isPanelShown(), !queue.quietTrack else { return }
        Notifier.shared.post(String(format: tr("Now on TV: %@"), title))
    }

    /// The panel's own poll runs only while it is open or something casts; this one covers
    /// casts started from the extension / hotkey with the panel closed, and the queue.
    private func tick() async {
        guard let state, state.panel != .casting, !isPanelShown() else { return }
        guard Date() < watchUntil || queue.isActive else { return }
        await state.refreshStatus()
    }

    private func watch(seconds: TimeInterval) {
        watchUntil = max(watchUntil, Date().addingTimeInterval(seconds))
    }

    // MARK: - casting from outside the panel

    /// ⌃⌥⌘C: the frontmost browser's tab. Chrome / Safari are read directly (AppleScript, as
    /// upstream's ⌘⇧C); any other browser through the tab its extension reported.
    func castFrontTab() async {
        guard let state else { return }
        let front = NSWorkspace.shared.frontmostApplication
        let frontName = front?.localizedName ?? ""
        let scriptable = Browser.allCases.contains { $0.bundleIdentifier == front?.bundleIdentifier }
        if let tab = browserTab, tab.fresh, !frontName.isEmpty,
           frontName.localizedCaseInsensitiveContains(tab.browser) || !scriptable {
            await castLink(tab.url, title: tab.title, start: tab.resumeAt ?? MediaFiles.urlStart(tab.url),
                           mediaSrc: tab.mediaSrc)
            return
        }
        queue.beforeOtherCast()
        watch(seconds: 60)
        await state.castFrontmostTab()
    }

    /// A link with an optional start; `mediaSrc` is the page's own stream file, used when
    /// the page itself isn't something catt can open.
    func castLink(_ url: String, title: String? = nil, start: TimeInterval?, mediaSrc: String? = nil) async {
        guard let state else { return }
        var target = url
        var kind = URLClassifier.classify(url)
        if let src = mediaSrc, !kind.isCastable, URLClassifier.classify(src) == .directMedia {
            target = src
            kind = .directMedia
        }
        if case .notCastable(let reason) = kind, reason == "Not a video page" {
            kind = .extractableSite          // asked for from the browser: let yt-dlp try the page
        }
        queue.beforeOtherCast()
        watch(seconds: 60)
        await state.castLink(target, kind: kind, seekTo: start)
    }

    func castClipboard() async {
        guard let state else { return }
        guard let raw = ClipboardReader.read() else { state.lastError = "Clipboard is empty"; return }
        let path = raw.hasPrefix("file://") ? (URL(string: raw)?.path ?? raw) : raw
        if path.hasPrefix("/"), FileManager.default.fileExists(atPath: path) {
            queue.send(paths: [path])                                   // a copied file path
            return
        }
        guard MediaFiles.isWeb(raw) else { state.lastError = "Not a web page"; return }
        await castLink(raw, start: MediaFiles.urlStart(raw))
    }

    // MARK: - files

    func pickFiles(slideshow: Bool = false) {
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = !slideshow
        p.allowsMultipleSelection = !slideshow
        p.message = tr(slideshow ? "Choose a folder with photos" : "Choose videos, music, photos, playlists or folders")
        p.prompt = tr("Play on TV")
        if let dir = settings.mediaDir { p.directoryURL = URL(fileURLWithPath: dir) }
        NSApp.activate(ignoringOtherApps: true)
        guard p.runModal() == .OK, !p.urls.isEmpty else { return }
        let paths = p.urls.map(\.path)
        settings.mediaDir = slideshow ? paths[0] : (paths[0] as NSString).deletingLastPathComponent
        watch(seconds: 60)
        if slideshow { queue.startSlideshow(folder: paths[0]) } else { queue.send(paths: paths) }
    }

    // MARK: - hotkey, bridge

    func setHotkey(_ on: Bool) {
        if on {
            if hotkey == nil {
                hotkey = GlobalHotkey { Task { @MainActor in await PortServices.shared.castFrontTab() } }
            }
        } else {
            hotkey = nil
        }
    }

    private func startBridge() {
        let d = UserDefaults.standard
        let port = UInt16(exactly: d.integer(forKey: "bridge_port")).flatMap { $0 >= 1024 ? $0 : nil }
            ?? BridgeHTTP.defaultPort
        let ids = Set((d.stringArray(forKey: "bridge_extension_ids") ?? []).filter { !$0.isEmpty })
        let b = BrowserBridge(port: port, allowedIDs: ids) { path, body in
            try await PortServices.shared.bridgeRequest(path, body)
        }
        if !b.start() { bridgeError = b.error }
        bridge = b
    }

    var bridgePort: UInt16 { bridge?.port ?? BridgeHTTP.defaultPort }
    var bridgeRunning: Bool { bridge != nil && bridge?.error == nil }

    /// Requests from the extension → actions, then the app's state as JSON (fling_app.py bridge_handler).
    func bridgeRequest(_ path: String, _ body: [String: Any]) async throws -> [String: Any] {
        guard let state else { throw BridgeError("starting") }
        if !settings.extensionSeen { settings.extensionSeen = true }
        if path == "/v1/ping" || path == "/v1/status" { return snapshot() }
        if path == "/v1/tab" {
            let url = String((body["url"] as? String ?? "").prefix(4096))
            if MediaFiles.isWeb(url) {
                let media = body["media"] as? [String: Any]
                let src = (media?["src"] as? String).flatMap { MediaFiles.isWeb($0) ? $0 : nil }
                browserTab = BrowserTab(url: url, title: String((body["title"] as? String ?? "").prefix(300)),
                                        browser: String((body["browser"] as? String ?? "Browser").prefix(40)),
                                        resumeAt: Self.resumePoint(media), mediaSrc: src, at: Date())
            }
            return ["ok": true]
        }
        let action = body["action"] as? String ?? ""
        func number(_ k: String) -> Double? { (body[k] as? NSNumber)?.doubleValue }
        switch action {
        case "cast":
            let url = (body["url"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            // web links only: a local path here would be served to the whole LAN by catt
            guard MediaFiles.isWeb(url) else { return ["ok": false, "error": tr("Not a web page")] }
            let src = (body["media_src"] as? String).flatMap { MediaFiles.isWeb($0) ? $0 : nil }
            let start = number("start").flatMap { $0 > 3 ? $0 : nil }
            bridgeCasting = true
            state.lastError = nil
            Task {
                await castLink(url, title: body["title"] as? String, start: start, mediaSrc: src)
                bridgeCasting = false
            }
            var snap = snapshot()
            snap["casting"] = true
            snap["error"] = NSNull()
            return snap
        case "play_pause": Task { await state.togglePlayPause() }
        case "seek":
            let s = Int(max(-3600, min(3600, number("seconds") ?? Double(AppState.seekStep))))
            Task { await state.seek(by: s) }
        case "stop":
            queue.stop()
            Task { await state.stopCasting() }
        case "next": queue.next()
        case "prev": queue.previous()
        case "volume": state.setVolume(Int(number("level") ?? Double(state.volume)))
        case "key":
            guard let code = Self.keys[body["key"] as? String ?? ""] else {
                throw BridgeError("unknown key \(body["key"] ?? "")")
            }
            Task { await state.pressTVKey(code) }
        case "launch":
            let name = (body["app"] as? String ?? "").lowercased()
            guard let app = TVApp.catalog.first(where: { $0.name.lowercased() == name }) else {
                throw BridgeError("unknown app")
            }
            Task { await state.launchTVApp(app) }
        case "text": Task { await state.sendTVText(String((body["text"] as? String ?? "").prefix(500))) }
        case "wake": Task { await state.wakeTV() }
        case "select_device":
            if let d = state.devices.first(where: { $0.ip == body["ip"] as? String }) {
                Task { await state.select(device: d) }
            }
        case "notify_track": settings.notifyTrack = (body["on"] as? Bool) ?? !settings.notifyTrack
        case "rescan", "refresh": Task { await state.refresh() }
        case "open_panel", "open_setup": openPanel()
        case "pair_remote":
            openPanel()
            Task { await state.startTVPairing() }
        default:
            throw BridgeError("unknown action \(action)")
        }
        return snapshot()
    }

    /// Android key codes by the extension's names (fling_app.py KEY).
    static let keys: [String: Int32] = [
        "power": 26, "home": 3, "back": 4, "up": 19, "down": 20, "left": 21, "right": 22, "ok": 23,
        "vol_up": 24, "vol_down": 25, "mute": 164, "play_pause": 85, "rewind": 89, "ffwd": 90,
    ]

    /// The extension's resumePoint(): well into the video and not near its end.
    static func resumePoint(_ m: [String: Any]?) -> TimeInterval? {
        guard let m, m["ended"] as? Bool != true,
              let t = (m["time"] as? NSNumber)?.doubleValue,
              let d = (m["duration"] as? NSNumber)?.doubleValue, d > 0,
              t >= 5, d - t >= 10 else { return nil }
        return t.rounded(.down)
    }

    func snapshot() -> [String: Any] {
        guard let state else { return ["ok": false] }
        let panel: String
        switch state.panel {
        case .setupNeeded: panel = "setup"
        case .casting: panel = "casting"
        case .idleCastable: panel = "idle_castable"
        case .idleNotCastable: panel = "idle_not_castable"
        }
        let st = state.status
        let mediaState: String? = st.hasMedia ? st.state.rawValue.uppercased() : nil
        func opt(_ v: Any?) -> Any { v ?? NSNull() }
        return [
            "ok": true, "app": "fling", "os": "macOS", "lang": Lang.current,
            "version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            "catt": state.panel != .setupNeeded,
            "device": state.selectedDevice.map { ["name": $0.name, "ip": $0.ip] as [String: Any] } ?? NSNull(),
            "devices": state.devices.map { ["name": $0.name, "ip": $0.ip] },
            "scanning": state.devices.isEmpty,
            "panel": panel, "casting": state.castInFlight || bridgeCasting,
            "media": ["state": opt(mediaState == "UNKNOWN" ? "PLAYING" : mediaState), "title": opt(st.title),
                      "elapsed": opt(st.elapsed), "duration": opt(st.duration),
                      "subtitle": opt(queue.isLive ? queue.positionLabel : nil)] as [String: Any],
            "volume": state.volume,
            "remote": ["paired": state.tvPaired, "on": opt(state.tvIsOn),
                       "app": opt(state.tvCurrentApp.map(TVApp.displayName(forPackage:)))] as [String: Any],
            "youtube_linked": false,
            "yt_live": queue.isLive,          // the popup shows previous / next: the file queue's here
            "notify_track": settings.notifyTrack,
            "apps": TVApp.catalog.map(\.name),
            "error": opt(state.lastError.map { tr($0) }),
        ]
    }

    // MARK: - settings window

    private var settingsWindow: NSWindow?

    func openSettings() {
        if settingsWindow == nil { settingsWindow = SettingsWindow.make() }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }
}
