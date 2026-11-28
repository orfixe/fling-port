// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: Samsung TVs as ordinary TVs of the panel (desktop `Hub` in
/// fling_samsung.py). AppState routes keys, typing, apps, casting and power here
/// when a Samsung TV is selected. Screen mirroring is not here: macOS has no
/// Miracast; Samsung TVs from 2018 on take AirPlay, which is macOS's own
/// Screen Mirroring (see docs/smart-view.md).
@MainActor
public final class SamsungHub {
    public let remote: SamsungRemote
    /// Called on every remote state change (AppState republishes).
    public var onChange: (() -> Void)?
    /// A status line for the panel (English; the panel translates), nil clears ours.
    public var onMessage: ((String?) -> Void)?

    public private(set) var known: [SamsungTV]
    private let defaults: UserDefaults
    private let discover: () async -> SamsungDiscovery.Found
    private let probe: (String) async -> SamsungTV?
    private var dlnaLocation: [String: String] = [:]
    private var dlnaControl: [String: URL] = [:]
    private var playing = false
    private var wakeTask: Task<Void, Never>?
    static let knownKey = "samsungTVs"

    public init(remote: SamsungRemote? = nil, defaults: UserDefaults = .standard,
                discover: (() async -> SamsungDiscovery.Found)? = nil,
                probe: ((String) async -> SamsungTV?)? = nil) {
        self.remote = remote ?? SamsungRemote()
        self.defaults = defaults
        self.discover = discover ?? { await SamsungDiscovery.discover() }
        self.probe = probe ?? { await SamsungDiscovery.info(ip: $0) }
        self.known = (defaults.data(forKey: Self.knownKey))
            .flatMap { try? JSONDecoder().decode([SamsungTV].self, from: $0) } ?? []
        self.remote.onState = { [weak self] st in self?.remoteChanged(st) }
    }

    // MARK: - the list

    public func tv(ip: String) -> SamsungTV? { known.first { $0.ip == ip } }
    public var current: SamsungTV? { remote.tv }
    public var ready: Bool { remote.state == .ready }

    /// Samsung TVs on the network plus the ones Fling knew (offline ones stay listed).
    public func scan() async -> [SamsungTV] {
        let found = await discover()
        dlnaLocation.merge(found.dlna) { $1 }
        var all = found.tvs
        let missing = known.filter { k in !all.contains { $0.ip == k.ip || (!k.mac.isEmpty && $0.mac == k.mac) } }
        for var k in missing {
            if let again = await probe(k.ip) { all.append(again) } else { k.isOn = false; all.append(k) }
        }
        remember(all)
        return all
    }

    /// Add by IP: is a Samsung answering there?
    public func add(ip: String) async -> SamsungTV? {
        guard let tv = await probe(ip) else { return nil }
        remember([tv])
        return tv
    }

    private func remember(_ tvs: [SamsungTV]) {
        for tv in tvs {
            known.removeAll { $0.ip == tv.ip || (!tv.mac.isEmpty && $0.mac == tv.mac) }
            known.append(tv)
        }
        if let data = try? JSONEncoder().encode(known) { defaults.set(data, forKey: Self.knownKey) }
    }

    // MARK: - selection and the remote

    public func select(ip: String?) {
        wakeTask?.cancel()
        playing = false
        if let ip, let tv = tv(ip: ip) {
            if remote.tv?.ip != ip || remote.state != .ready { remote.connect(tv) }
        } else {
            remote.close()
        }
    }

    /// The setup row's "Set Up TV Power…" for a Samsung: connect, the TV asks Allow.
    public func connect() {
        guard let tv = remote.tv else { return }
        remote.connect(tv)
    }

    private func remoteChanged(_ st: SamsungRemote.State) {
        switch st {
        case .ready, .off, .connecting: onMessage?(nil)
        case .waiting: onMessage?(SamsungText.pressAllow)
        case .denied: onMessage?(SamsungText.denied)
        case .error: onMessage?(SamsungText.noAnswer)
        }
        onChange?()
    }

    public var statusLine: String {
        switch remote.state {
        case .ready: return "Samsung · network remote"
        case .waiting: return SamsungText.pressAllow
        case .connecting: return "Connecting to the TV…"
        case .denied: return "Samsung · not allowed on the TV"
        case .off, .error: return "Samsung · the TV doesn't answer"
        }
    }

    private func reconnect() {
        if let tv = remote.tv, [.off, .error].contains(remote.state) { remote.connect(tv) }
    }

    // MARK: - keys, text, apps

    public func key(_ code: Int32) {
        if code == 26 || code == 224, remote.state != .ready {      // power with no remote: wake it
            powerOn(); return
        }
        guard let name = SamsungProtocol.androidKeys[code] else { return }
        if !remote.key(name) { reconnect() }
    }

    public func text(_ s: String) {
        guard !s.isEmpty else { return }
        if !remote.text(s) { reconnect() }
    }

    /// An app from the panel's grid, by its name.
    public func launch(appNamed name: String) {
        guard let app = SamsungProtocol.app(named: name, installed: remote.apps) else {
            onMessage?(SamsungText.unknownApp); return
        }
        if !remote.launch(appId: app.id, type: app.type) { reconnect(); onMessage?(SamsungText.noAnswer) }
    }

    // MARK: - power

    public func powerOn() {
        guard let tv = remote.tv ?? known.first else { return }
        guard SamsungDiscovery.wake(mac: tv.mac, ip: tv.ip) else {
            onMessage?(SamsungText.noMAC); return
        }
        onMessage?("Turning the TV on…")
        wakeTask?.cancel()
        let probe = self.probe
        wakeTask = Task { [weak self] in
            for _ in 0..<10 {
                try? await Task.sleep(for: .milliseconds(1500))
                if Task.isCancelled { return }
                if let info = await probe(tv.ip), info.isOn {
                    guard let self, !Task.isCancelled else { return }
                    self.onMessage?(nil)
                    self.remote.connect(info)
                    return
                }
            }
            self?.onMessage?(SamsungText.powerOnHint)
        }
    }

    // MARK: - casting

    /// A link to the TV: media files over DLNA, YouTube in its YouTube app,
    /// everything else in the TV's browser. Returns an error line, nil on success.
    public func cast(_ url: String, kind: CastKind) async -> String? {
        guard let tv = remote.tv else { return "No device selected" }
        switch kind {
        case .notCastable(let reason):
            return reason
        case .directMedia:
            return await playDLNA(url, title: URL(string: url)?.lastPathComponent ?? url, on: tv)
        case .youtube:
            let meta = SamsungProtocol.youtubeID(url).map { "v=\($0)" } ?? ""
            if remote.launch(appId: SamsungProtocol.youtubeApp, type: 2, meta: meta) { return nil }
        case .extractableSite:
            if remote.launch(appId: SamsungProtocol.browserApp, type: 4, meta: url) { return nil }
        }
        reconnect()
        return SamsungText.noAnswer
    }

    func playDLNA(_ url: String, title: String, on tv: SamsungTV) async -> String? {
        var control = dlnaControl[tv.ip]
        if control == nil {
            control = await SamsungDLNA.findControl(ip: tv.ip, location: dlnaLocation[tv.ip])
        }
        guard let control else { return SamsungText.noDLNA }
        dlnaControl[tv.ip] = control
        do {
            try await SamsungDLNA.play(control, url: url, title: title, mime: SamsungDLNA.mime(for: url))
            playing = true
            return nil
        } catch {
            return "The TV didn't accept the video (\(Self.describe(error)))."
        }
    }

    /// "Toggle" (play / pause) or "Stop": over DLNA when Fling sent something
    /// there, else the remote's play / stop keys.
    public func transport(_ action: String) async {
        if let ip = remote.tv?.ip, let control = dlnaControl[ip] {
            let soapAction = action == "Stop" ? "Stop" : (playing ? "Pause" : "Play")
            let args = soapAction == "Play" ? "<Speed>1</Speed>" : ""
            if (try? await SamsungDLNA.soap(control, action: soapAction, args: args)) != nil {
                playing = soapAction == "Play"
                return
            }
        }
        key(action == "Stop" ? 86 : 85)
    }

    static func describe(_ error: Error) -> String {
        if case SamsungDLNA.Failure.soap(let status, _) = error { return "HTTP \(status)" }
        return (error as NSError).localizedDescription
    }
}

/// The hub's longer lines, in one place for L10n.
public enum SamsungText {
    public static let pressAllow = "Press Allow on the TV screen"
    public static let denied = "The TV refused the connection. On the TV: General → External Device Manager → Device Connection Manager — allow Fling."
    public static let noAnswer = "The TV doesn't answer — it's off or on another network."
    public static let unknownApp = "Fling doesn't know this app on Samsung — open it with the remote."
    public static let noMAC = "Fling doesn't know the TV's MAC address yet — turn the TV on once with its remote so Fling can remember it."
    public static let powerOnHint = "To turn on over the network, on the TV: General → Network → Expert Settings → Power On with Mobile."
    public static let noDLNA = "The TV doesn't support DLNA or it's off."
    public static let noVoice = "Voice search isn't available on Samsung TVs — type instead."
}
