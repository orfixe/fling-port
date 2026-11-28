// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: what kind of TV a list entry is (the Windows build's 0.5.4 "sorted by
/// kind"): Android TV / Google TV, then Chromecast, then Samsung.
public enum DeviceKind: Int, Comparable, Sendable {
    case androidTV, chromecast, samsung

    public static func < (a: DeviceKind, b: DeviceKind) -> Bool { a.rawValue < b.rawValue }

    /// English; the panel passes it through tr().
    public var label: String {
        switch self {
        case .androidTV: return "Google TV / Android TV"
        case .chromecast: return "Chromecast"
        case .samsung: return "Samsung"
        }
    }

    /// Cast devices whose model says it's a TV running Android / Google TV.
    static func castKind(model: String) -> DeviceKind {
        let m = model.lowercased()
        let tvWords = ["google tv", "android tv", "shield", "bravia", "mibox", "mi box", "tcl", "hisense",
                       "philips", "sony", "smart tv"]
        return tvWords.contains { m.contains($0) } ? .androidTV : .chromecast
    }
}

public extension AppState {

    /// Turns Samsung (Smart View) support on: discovery joins the TV search,
    /// the remote, apps and casting go to a selected Samsung TV.
    func enableSamsung(_ hub: SamsungHub? = nil) {
        let hub = hub ?? SamsungHub()
        samsung = hub
        hub.onChange = { [weak self] in self?.samsungRemoteChanged() }
        hub.onMessage = { [weak self] line in self?.samsungMessage(line) }
    }

    /// The selected TV when it's a Samsung.
    var selectedSamsung: SamsungTV? {
        guard let ip = selectedDevice?.ip else { return nil }
        return samsung?.tv(ip: ip)
    }

    var isSamsungSelected: Bool { selectedSamsung != nil }

    /// The Samsung status line for the panel, nil for other TVs.
    var samsungStatus: String? { isSamsungSelected ? samsung?.statusLine : nil }

    func kind(of device: DeviceInfo) -> DeviceKind {
        if samsung?.tv(ip: device.ip) != nil { return .samsung }
        if atv?.hasPairing(host: device.ip) == true { return .androidTV }
        return DeviceKind.castKind(model: device.model)
    }

    /// The list as the chooser shows it: by kind, then the current TV, then by name.
    var devicesByKind: [DeviceInfo] {
        let current = selectedDevice?.ip
        return devices.sorted { a, b in
            let ka = kind(of: a), kb = kind(of: b)
            if ka != kb { return ka < kb }
            if (a.ip == current) != (b.ip == current) { return a.ip == current }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    /// Chooser label: "Living room — Samsung".
    func chooserLabel(_ device: DeviceInfo, translate: (String) -> String = { $0 }) -> String {
        "\(device.name) — \(translate(kind(of: device).label))"
    }

    /// Cast devices, then Samsung TVs; a Samsung also seen over Cast is listed once,
    /// as Samsung. (The chooser sorts by kind: `devicesByKind`.)
    func mergeDevices(cast: [DeviceInfo], samsung tvs: [SamsungTV]) -> [DeviceInfo] {
        let sv = tvs.map(\.device)
        let ips = Set(sv.map(\.ip))
        return cast.filter { !ips.contains($0.ip) } + sv
    }

    // MARK: - routing (called from AppState when a Samsung TV is selected)

    internal func samsungScan() async -> [SamsungTV] {
        guard let samsung else { return [] }
        return await samsung.scan()
    }

    internal func samsungSelectionChanged() {
        samsung?.select(ip: selectedSamsung?.ip)
    }

    private func samsungRemoteChanged() {
        if let s = samsung, isSamsungSelected {
            tvPaired = s.ready
            tvIsOn = s.ready ? true : nil
        }
        objectWillChange.send()
    }

    private func samsungMessage(_ line: String?) {
        guard isSamsungSelected else { return }
        if let line {
            lastError = line
        } else if let e = lastError, Self.samsungLines.contains(e) {
            lastError = nil
        }
    }

    private static let samsungLines: Set<String> = [
        SamsungText.pressAllow, SamsungText.denied, SamsungText.noAnswer, SamsungText.unknownApp,
        SamsungText.noMAC, SamsungText.powerOnHint, SamsungText.noDLNA, "Turning the TV on…",
    ]

    internal func samsungKey(_ code: Int32) { samsung?.key(code) }
    internal func samsungText(_ text: String) { samsung?.text(text) }
    internal func samsungLaunch(_ app: TVApp) { samsung?.launch(appNamed: app.name) }

    internal func samsungCast(_ url: String, kind: CastKind) async {
        guard let samsung else { return }
        lastError = await samsung.cast(url, kind: kind)
    }
}
