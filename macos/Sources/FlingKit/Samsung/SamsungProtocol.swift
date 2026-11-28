// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: Smart View for Samsung TVs (Tizen, 2016 and later) — the pure protocol
/// pieces, the same as desktop/fling_samsung.py: the TV's network remote (a
/// WebSocket on 8002, 8001 on older sets), its app ids, its /api/v2/ description,
/// SSDP answers and Wake-on-LAN. No I/O here, so all of it is unit-tested.
public enum SamsungProtocol {
    public static let tlsPort = 8002
    public static let plainPort = 8001
    public static let dlnaPort = 9197
    public static let channel = "/api/v2/channels/samsung.remote.control"
    /// What the TV shows in its "Allow?" prompt and in Device Connection Manager.
    public static let clientName = "Fling (Mac)"
    public static let browserApp = "org.tizen.browser"
    public static let youtubeApp = "111299001912"

    // MARK: - the remote WebSocket

    public static func remoteURL(host: String, token: String?, plain: Bool,
                                 tlsPort: Int = tlsPort, plainPort: Int = plainPort) -> URL? {
        var c = URLComponents()
        c.scheme = plain ? "ws" : "wss"
        c.host = host
        c.port = plain ? plainPort : tlsPort
        c.path = channel
        var q = [URLQueryItem(name: "name", value: Data(clientName.utf8).base64EncodedString())]
        if let token, !token.isEmpty { q.append(URLQueryItem(name: "token", value: token)) }
        c.queryItems = q
        return c.url
    }

    static func json(_ obj: [String: Any]) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }

    public static func keyMessage(_ key: String, cmd: String = "Click") -> String {
        json(["method": "ms.remote.control", "params": [
            "Cmd": cmd, "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"]])
    }

    /// Whole field text: the TV's on-screen keyboard takes the string as it is now.
    public static func textMessage(_ text: String) -> String {
        json(["method": "ms.remote.control", "params": [
            "Cmd": Data(text.utf8).base64EncodedString(), "DataOfCmd": "base64",
            "TypeOfRemote": "SendInputString"]])
    }

    public static let inputEndMessage =
        json(["method": "ms.remote.control", "params": ["TypeOfRemote": "SendInputEnd"]])

    /// `appType` 4 = a native app (the browser), 2 = a DEEP_LINK app (YouTube…).
    public static func launchMessage(appId: String, appType: Int = 2, meta: String = "") -> String {
        json(["method": "ms.channel.emit", "params": [
            "event": "ed.apps.launch", "to": "host",
            "data": ["appId": appId, "action_type": appType == 4 ? "NATIVE_LAUNCH" : "DEEP_LINK",
                     "metaTag": meta]]])
    }

    public static let installedAppsMessage =
        json(["method": "ms.channel.emit", "params": ["event": "ed.installedApp.get", "to": "host"]])

    public enum Event: Equatable, Sendable {
        /// The TV let us in; `token` is set on TLS sets the first time (keep it).
        case connected(token: String?)
        case unauthorized
        case timeOut
        case installedApps([SamsungApp])
        case other
    }

    public static func parse(_ text: String) -> Event {
        guard let obj = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any],
              let ev = obj["event"] as? String else { return .other }
        let data = obj["data"] as? [String: Any]
        switch ev {
        case "ms.channel.connect":
            let tok = data?["token"].map { "\($0)" }
            return .connected(token: tok?.isEmpty == false ? tok : nil)
        case "ms.channel.unauthorized": return .unauthorized
        case "ms.channel.timeOut": return .timeOut
        case "ed.installedApp.get":
            let list = (data?["data"] as? [[String: Any]]) ?? []
            return .installedApps(list.compactMap { a in
                guard let id = a["appId"] as? String, let name = a["name"] as? String,
                      !id.isEmpty, !name.isEmpty else { return nil }
                return SamsungApp(name: name, id: id, type: (a["app_type"] as? Int) ?? 2)
            })
        default: return .other
        }
    }

    // MARK: - keys

    /// Android key codes (what the panel's remote and the Mac keyboard send) →
    /// Samsung remote keys. Same table as the Windows / Linux build.
    public static let androidKeys: [Int32: String] = {
        var m: [Int32: String] = [
            19: "KEY_UP", 20: "KEY_DOWN", 21: "KEY_LEFT", 22: "KEY_RIGHT", 23: "KEY_ENTER",
            66: "KEY_ENTER", 4: "KEY_RETURN", 111: "KEY_RETURN", 3: "KEY_HOME", 82: "KEY_MENU",
            24: "KEY_VOLUP", 25: "KEY_VOLDOWN", 164: "KEY_MUTE", 26: "KEY_POWER", 223: "KEY_POWER",
            85: "KEY_PLAY", 126: "KEY_PLAY", 127: "KEY_PAUSE", 86: "KEY_STOP", 89: "KEY_REWIND",
            90: "KEY_FF", 87: "KEY_FF", 88: "KEY_REWIND", 166: "KEY_CHUP", 167: "KEY_CHDOWN",
            178: "KEY_SOURCE", 165: "KEY_INFO", 172: "KEY_GUIDE", 183: "KEY_RED",
            184: "KEY_GREEN", 185: "KEY_YELLOW", 186: "KEY_CYAN", 67: "KEY_BACKSPACE",
            84: "KEY_SEARCH",
        ]
        for i in 0..<10 { m[Int32(7 + i)] = "KEY_\(i)" }
        return m
    }()

    // MARK: - apps

    /// Tizen ids of the apps in the panel's grid (TVApp.catalog, by name). An app
    /// with no known id is looked up by name in the TV's own installed list.
    public static let knownApps: [String: String] = [
        "YouTube": "111299001912", "Netflix": "3201907018807", "Prime Video": "3201910019365",
        "Disney+": "3201901017640", "Spotify": "3201606009684", "Plex": "3201512006963",
        "Apple TV": "3201807016597", "Twitch": "3202203026841",
    ]

    /// The app to open for a panel app name: the TV's own list first, then the known id.
    public static func app(named name: String, installed: [SamsungApp]) -> SamsungApp? {
        let low = name.lowercased()
        if let a = installed.first(where: { $0.name.lowercased() == low })
            ?? installed.first(where: { $0.name.lowercased().contains(low) }) {
            return a
        }
        return knownApps[name].map { SamsungApp(name: name, id: $0, type: 2) }
    }

    /// The 11-character video id of a YouTube link, for the TV's YouTube app.
    public static func youtubeID(_ url: String) -> String? {
        let rx = try! NSRegularExpression(pattern: #"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})"#)
        let r = NSRange(url.startIndex..., in: url)
        guard let m = rx.firstMatch(in: url, range: r), let g = Range(m.range(at: 1), in: url) else { return nil }
        return String(url[g])
    }

    // MARK: - /api/v2/ (the TV's own description)

    public static func deviceInfo(json data: Data, ip: String) -> SamsungTV? {
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let dev = obj["device"] as? [String: Any], !dev.isEmpty else { return nil }
        let raw = (dev["name"] as? String) ?? (obj["name"] as? String) ?? "Samsung TV"
        let model = (dev["modelName"] as? String) ?? (dev["model"] as? String) ?? ""
        let mac = ((dev["wifiMac"] as? String) ?? "").lowercased()
        let power = ((dev["PowerState"] as? String) ?? "on").lowercased()
        return SamsungTV(ip: ip, name: unescape(raw), model: model, mac: mac, isOn: power == "on")
    }

    static func unescape(_ s: String) -> String {
        var out = s
        for (k, v) in [("&quot;", "\""), ("&#39;", "'"), ("&apos;", "'"), ("&lt;", "<"),
                       ("&gt;", ">"), ("&amp;", "&")] {
            out = out.replacingOccurrences(of: k, with: v)
        }
        return out
    }

    // MARK: - SSDP

    public static let searchTargets = ["urn:samsung.com:device:RemoteControlReceiver:1",
                                       "urn:schemas-upnp-org:device:MediaRenderer:1"]

    public static func mSearch(_ st: String) -> String {
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: \(st)\r\n\r\n"
    }

    public struct SSDPAnswer: Equatable, Sendable {
        public let st: String
        public let location: String?
        public let server: String?
        /// Worth asking /api/v2/: a Samsung remote, a Samsung server or any DLNA renderer.
        public var maybeSamsung: Bool {
            st.lowercased().contains("samsung") || (server?.lowercased().contains("samsung") ?? false)
                || st.contains("MediaRenderer")
        }
        public var isRenderer: Bool { st.contains("MediaRenderer") }
    }

    public static func parseSSDP(_ text: String) -> SSDPAnswer? {
        var headers: [String: String] = [:]
        let lines = text.components(separatedBy: "\n").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard let first = lines.first, first.uppercased().hasPrefix("HTTP/1.1 200")
                || first.uppercased().hasPrefix("NOTIFY") else { return nil }
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let k = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            let v = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            headers[k] = v
        }
        let st = headers["st"] ?? headers["nt"] ?? ""
        return SSDPAnswer(st: st, location: headers["location"], server: headers["server"])
    }

    // MARK: - Wake-on-LAN

    /// The magic packet for `mac` (any separators), nil when it isn't a MAC.
    public static func magicPacket(mac: String) -> Data? {
        let hex = mac.filter(\.isHexDigit)
        guard hex.count == 12 else { return nil }
        var bytes = [UInt8](repeating: 0xff, count: 6)
        var macBytes: [UInt8] = []
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2)
            macBytes.append(UInt8(hex[i..<j], radix: 16)!)
            i = j
        }
        for _ in 0..<16 { bytes += macBytes }
        return Data(bytes)
    }
}

/// An app on a Samsung TV: its Tizen id and how to launch it.
public struct SamsungApp: Equatable, Hashable, Sendable {
    public let name: String
    public let id: String
    public let type: Int
    public init(name: String, id: String, type: Int) { self.name = name; self.id = id; self.type = type }
}

/// A Samsung TV as its /api/v2/ describes it (and as Fling remembers it).
public struct SamsungTV: Equatable, Hashable, Codable, Sendable {
    public var ip: String
    public var name: String
    public var model: String
    public var mac: String
    public var isOn: Bool
    public init(ip: String, name: String, model: String, mac: String, isOn: Bool = true) {
        self.ip = ip; self.name = name; self.model = model; self.mac = mac; self.isOn = isOn
    }
    /// The token is kept per TV: by MAC when the TV tells it (the IP can change), else by IP.
    public var tokenKey: String { mac.isEmpty ? ip : mac }

    public var device: DeviceInfo {
        DeviceInfo(ip: ip, name: name, model: model.isEmpty ? "Samsung" : "Samsung \(model)")
    }
}
