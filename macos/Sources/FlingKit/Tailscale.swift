// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Network

// Port: remote access over Tailscale — the TV at home, Fling anywhere (same as
// desktop/fling_tailscale.py in the Windows / Linux build).
//
// Fling embeds no VPN code. It relies on the official Tailscale app on the Mac and
// on the TV and only *reads* the Mac's Tailscale state (`tailscale status --json`,
// `tailscale ping`); it never runs `up`, `down`, `set` or `logout`, never changes
// the tailnet's settings and never opens anything to the internet.
//
// What works over the tailnet: everything that connects to the TV by address — the
// Android TV Remote (6466/6467), ADB (5555) and LIVE on top of ADB. Casting through
// catt needs the Cast port (8009) and mDNS discovery, which stay on the home network;
// a tailnet TV whose Cast port doesn't answer gets a plain-words message instead.
//
// API for the rest of the app (ADB, LIVE, …): a tailnet TV is an ordinary
// `DeviceInfo` whose `ip` is its Tailscale address, so code that connects to
// `selectedDevice.ip` already works away from home. On top of that:
//   * `device.isTailnet` / `Tailnet.isTailnetAddress(ip)` — reached over Tailscale?
//   * `Tailnet.prefersSaverQuality(device)` — LIVE should start light (relayed / mobile);
//   * `TailnetTVStore.shared.entry(ip:)` — what probing found (Cast / remote / ADB);
//   * `Tailnet.castBlockedReason(device)` — the message to show instead of casting.

// MARK: - addresses and naming

public enum Tailnet {
    /// Cast, Android TV Remote, ADB: any one answering makes an address a TV worth adding.
    public static let tvPorts: [UInt16] = [8009, 6466, 5555]
    public static let castPort: UInt16 = 8009
    public static let downloadURL = URL(string: "https://tailscale.com/download/mac")!
    public static func docsURL(russian: Bool) -> URL {
        URL(string: russian ? "https://github.com/orfixe/fling-port/blob/main/docs/tailscale.ru.md"
                            : "https://github.com/orfixe/fling-port/blob/main/docs/tailscale.md")!
    }

    static func ipv4Octets(_ s: String) -> [Int]? {
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var out: [Int] = []
        for p in parts {
            guard !p.isEmpty, p.count <= 3, p.allSatisfy({ $0.isASCII && $0.isNumber }),
                  let v = Int(p), v <= 255 else { return nil }
            out.append(v)
        }
        return out
    }

    public static func isIPv4(_ s: String) -> Bool { ipv4Octets(s) != nil }

    /// 100.64.0.0/10 (the addresses Tailscale gives its devices) or fd7a:115c:a1e0::/48.
    public static func isTailnetIP(_ ip: String) -> Bool {
        if let o = ipv4Octets(ip) { return o[0] == 100 && (64...127).contains(o[1]) }
        return ip.lowercased().hasPrefix("fd7a:115c:a1e0:")
    }

    /// Reached over Tailscale: a tailnet IP, or a TV the user added through Tailscale
    /// (a home address behind a subnet router counts too).
    public static func isTailnetAddress(_ ip: String, store: TailnetTVStore = .shared) -> Bool {
        isTailnetIP(ip) || store.entry(ip: ip) != nil
    }

    /// Should an address typed into "Add by address" go through Tailscale? A tailnet IP
    /// or a host name (MagicDNS: `tv` or `tv.tailnet.ts.net`); a plain LAN IP doesn't.
    public static func wantsTailscale(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespaces).lowercased()
        if isIPv4(t) { return isTailnetIP(t) }
        guard let first = t.first, !first.isNumber else { return false }
        return t.range(of: #"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?$"#,
                       options: .regularExpression) != nil
    }

    // Host names that say "TV": Android TV / Google TV sets and boxes name themselves after the model.
    private static let tvHints = ["tv", "chromecast", "bravia", "shield", "mibox", "mi-box", "mi box",
                                  "streamer", "philips", "tcl", "hisense", "nvidia", "onn", "sberbox",
                                  "yandex", "kivi", "xiaomi-tv", "box"]
    private static let notTV = ["phone", "pixel", "galaxy", "sm-", "iphone", "ipad", "tablet", "redmi",
                                "oneplus", "huawei", "honor", "moto", "nokia", "watch", "pad"]

    /// 2: looks like a TV; 1: an Android device that may be one; 0: something else.
    public static func tvScore(host: String, os: String) -> Int {
        let h = host.lowercased()
        guard os.lowercased().contains("android") else { return 0 }
        if notTV.contains(where: h.contains) {
            return (h.contains("tv") || h.contains("chromecast")) ? 1 : 0
        }
        return tvHints.contains(where: h.contains) ? 2 : 1
    }

    /// Does `cidr` (IPv4 "a.b.c.d/n") contain `ip`?
    public static func cidr(_ cidr: String, contains ip: String) -> Bool {
        let parts = cidr.split(separator: "/")
        guard parts.count == 2, let bits = Int(parts[1]), (0...32).contains(bits),
              let net = ipv4Octets(String(parts[0])), let addr = ipv4Octets(ip) else { return false }
        func u32(_ o: [Int]) -> UInt32 { o.reduce(0) { ($0 << 8) | UInt32($1) } }
        let mask: UInt32 = bits == 0 ? 0 : ~UInt32(0) << UInt32(32 - bits)
        return u32(net) & mask == u32(addr) & mask
    }

    /// A tailnet TV whose Cast port didn't answer: casting (catt) can't reach it.
    public static func castBlockedReason(_ device: DeviceInfo?, store: TailnetTVStore = .shared) -> String? {
        guard let device, let e = store.entry(ip: device.ip), e.cast == false else { return nil }
        return "This TV was added via Tailscale and its Cast port doesn't answer, so casting isn't available. The remote, apps and ADB work."
    }

    /// LIVE over the tailnet starts light: the path may be relayed or on mobile data.
    public static func prefersSaverQuality(_ device: DeviceInfo?) -> Bool {
        device?.isTailnet ?? false
    }

    /// The access rule the Security section suggests: only your devices, only Fling's ports.
    public static func aclExample(login: String = "") -> String {
        let who = login.isEmpty ? "you@example.com" : login
        return """
        {
          "tagOwners": {"tag:tv": ["\(who)"]},
          "acls": [
            // only your own devices reach the TV, and only the ports Fling uses
            {"action": "accept", "src": ["\(who)"],
             "dst": ["tag:tv:5555,6466,6467,8008,8009"]}
          ]
        }
        """
    }
}

public extension DeviceInfo {
    /// Reached over Tailscale (a tailnet address or a TV added through the tailnet).
    var isTailnet: Bool { Tailnet.isTailnetAddress(ip) }
}

// MARK: - `tailscale status --json`

public struct TailnetPeer: Equatable, Hashable, Sendable, Identifiable {
    public let id: String
    public let host: String
    /// Full MagicDNS name without the trailing dot ("tv.tail1234.ts.net").
    public let dns: String
    public let short: String
    public let ip: String
    public let os: String
    public let online: Bool
    /// DERP region of the home relay ("fra"), empty when unknown.
    public let relay: String
    /// A direct (not relayed) path is in use right now.
    public let direct: Bool
    public let tvScore: Int
    /// Subnet routes this peer serves (e.g. a home router advertising 192.168.1.0/24).
    public let routes: [String]

    public init(id: String, host: String, dns: String = "", ip: String, os: String = "android",
                online: Bool = true, relay: String = "", direct: Bool = false, routes: [String] = []) {
        self.id = id; self.host = host; self.dns = dns
        self.short = dns.split(separator: ".").first.map(String.init) ?? ""
        self.ip = ip; self.os = os; self.online = online; self.relay = relay
        self.direct = direct; self.routes = routes
        self.tvScore = Tailnet.tvScore(host: host, os: os)
    }

    public var looksLikeTV: Bool { tvScore > 0 }
}

public struct TailnetStatus: Equatable, Sendable {
    public let backendState: String
    public let me: TailnetPeer?
    /// TV-like peers first, then online ones, then by name.
    public let peers: [TailnetPeer]
    public let magicDNSSuffix: String
    public let login: String
    public let tailnetName: String

    public var running: Bool { backendState == "Running" }
    public var tvs: [TailnetPeer] { peers.filter(\.looksLikeTV) }
    /// Peers that route a home network into the tailnet.
    public var subnetRouters: [TailnetPeer] { peers.filter { !$0.routes.isEmpty } }

    /// A peer by tailnet IP, MagicDNS name, short name or host name.
    public func peer(matching text: String) -> TailnetPeer? {
        let t = text.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "."))
            .lowercased()
        guard !t.isEmpty else { return nil }
        return peers.first { [$0.ip, $0.dns.lowercased(), $0.short.lowercased(), $0.host.lowercased()].contains(t) }
    }

    /// The subnet router through which a home (LAN) address is reachable, if any.
    public func router(for ip: String) -> TailnetPeer? {
        subnetRouters.first { $0.routes.contains { Tailnet.cidr($0, contains: ip) } }
    }
}

public enum TailscaleParseError: Error, Equatable { case notJSON(String) }

public enum TailscaleStatusParser {
    public static func parse(_ data: Data) throws -> TailnetStatus {
        guard let js = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            let text = String(data: data, encoding: .utf8) ?? ""
            throw TailscaleParseError.notJSON(String(text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(200)))
        }
        let me = (js["Self"] as? [String: Any]).flatMap(peer)
        var login = ""
        if let uid = (js["Self"] as? [String: Any])?["UserID"], let users = js["User"] as? [String: Any],
           let u = users["\(uid)"] as? [String: Any] {
            login = u["LoginName"] as? String ?? ""
        }
        let peers = ((js["Peer"] as? [String: Any]) ?? [:]).values
            .compactMap { ($0 as? [String: Any]).flatMap(peer) }
            .sorted { a, b in
                if a.tvScore != b.tvScore { return a.tvScore > b.tvScore }
                if a.online != b.online { return a.online }
                return a.host.lowercased() < b.host.lowercased()
            }
        let tailnet = (js["CurrentTailnet"] as? [String: Any])?["Name"] as? String ?? ""
        return TailnetStatus(backendState: js["BackendState"] as? String ?? "", me: me, peers: peers,
                             magicDNSSuffix: js["MagicDNSSuffix"] as? String ?? "",
                             login: login, tailnetName: tailnet)
    }

    private static func peer(_ p: [String: Any]) -> TailnetPeer? {
        let ips = p["TailscaleIPs"] as? [String] ?? []
        guard let v4 = ips.first(where: Tailnet.isIPv4) else { return nil }
        let dns = (p["DNSName"] as? String ?? "").trimmingCharacters(in: CharacterSet(charactersIn: "."))
        var host = p["HostName"] as? String ?? ""
        if host.isEmpty || host == "localhost" { host = dns.split(separator: ".").first.map(String.init) ?? v4 }
        // PrimaryRoutes: the subnets this peer actually serves now; AllowedIPs minus its
        // own addresses as a fallback (older clients).
        var routes = p["PrimaryRoutes"] as? [String] ?? []
        if routes.isEmpty {
            routes = (p["AllowedIPs"] as? [String] ?? []).filter { r in
                !ips.contains { r.hasPrefix($0 + "/") } && r.contains(".") && r != "0.0.0.0/0"
            }
        }
        let id = (p["ID"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? (p["PublicKey"] as? String ?? v4)
        return TailnetPeer(id: id, host: host, dns: dns, ip: v4, os: p["OS"] as? String ?? "",
                           online: p["Online"] as? Bool ?? false, relay: p["Relay"] as? String ?? "",
                           direct: !((p["CurAddr"] as? String ?? "").isEmpty),
                           routes: routes.filter { $0.contains(".") && $0 != "0.0.0.0/0" })
    }
}

/// What the Mac's Tailscale says, in the terms the UI needs.
public enum TailnetState: Equatable, Sendable {
    case notInstalled
    /// The CLI ran but its answer couldn't be read.
    case unreadable(String)
    /// Installed but not connected: Stopped, NeedsLogin, NeedsMachineAuth, Starting…
    case off(backendState: String)
    case running(TailnetStatus)

    public var status: TailnetStatus? {
        if case .running(let s) = self { return s }
        return nil
    }
}

/// One round trip to a tailnet address.
public struct TailnetPing: Equatable, Sendable {
    public let ms: Double
    /// nil: unknown (measured without the CLI).
    public let direct: Bool?
    /// DERP relay region ("fra") when relayed.
    public let relay: String

    public init(ms: Double, direct: Bool?, relay: String) {
        self.ms = ms; self.direct = direct; self.relay = relay
    }

    /// `tailscale ping -c 1` output: "pong from tv (100.x) via DERP(fra) in 45ms" or
    /// "… via 192.168.1.20:41641 in 3ms".
    public static func parse(_ output: String) -> TailnetPing? {
        guard let rx = try? NSRegularExpression(pattern: #"pong from .*? via (.+?) in ([\d.]+)\s*ms"#),
              let m = rx.firstMatch(in: output, range: NSRange(output.startIndex..., in: output)),
              let viaR = Range(m.range(at: 1), in: output), let msR = Range(m.range(at: 2), in: output),
              let ms = Double(output[msR]) else { return nil }
        let via = output[viaR].trimmingCharacters(in: .whitespaces)
        var relay = ""
        if via.hasPrefix("DERP("), let close = via.firstIndex(of: ")") {
            relay = String(via[via.index(via.startIndex, offsetBy: 5)..<close])
        }
        return TailnetPing(ms: ms, direct: !(via.hasPrefix("DERP") || via.hasPrefix("peer-relay")), relay: relay)
    }
}

// MARK: - the Tailscale client on this Mac (read-only)

/// `@unchecked Sendable` like CattClient: immutable, and the production runner is stateless.
public final class TailscaleCLI: @unchecked Sendable {
    public let executable: String
    private let runner: ProcessRunning

    public init(executable: String, runner: ProcessRunning = SystemProcessRunner()) {
        self.executable = executable
        self.runner = runner
    }

    /// GUI apps don't inherit the shell's PATH: Homebrew / standalone CLI first, then the
    /// binary inside Tailscale.app (App Store and standalone builds both ship it there).
    public static let candidates = [
        "/opt/homebrew/bin/tailscale",
        "/usr/local/bin/tailscale",
        "/usr/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        NSHomeDirectory() + "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]

    public static func resolveExecutable(isExecutable: (String) -> Bool = {
        FileManager.default.isExecutableFile(atPath: $0)
    }) -> String? {
        candidates.first(where: isExecutable)
    }

    public static func find(runner: ProcessRunning = SystemProcessRunner()) -> TailscaleCLI? {
        resolveExecutable().map { TailscaleCLI(executable: $0, runner: runner) }
    }

    /// `tailscale status --json`. Blocking: call off the main actor.
    public func state() -> TailnetState {
        let result: ProcessResult
        do { result = try runner.run(executable, ["status", "--json"], timeout: 8) }
        catch { return .unreadable(error.localizedDescription) }
        guard let data = result.stdout.data(using: .utf8), !result.stdout.isEmpty,
              let status = try? TailscaleStatusParser.parse(data) else {
            let msg = (result.stderr.isEmpty ? result.stdout : result.stderr)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            // "Tailscale is stopped." / "Logged out." come back as text with a non-zero exit
            let low = msg.lowercased()
            if low.contains("stopped") { return .off(backendState: "Stopped") }
            if low.contains("logged out") || low.contains("needslogin") || low.contains("log in") {
                return .off(backendState: "NeedsLogin")
            }
            if low.contains("failed to connect") || low.contains("not running") {
                return .off(backendState: "NoState")
            }
            return .unreadable(String(msg.prefix(200)).isEmpty ? "tailscale status" : String(msg.prefix(200)))
        }
        return status.running ? .running(status) : .off(backendState: status.backendState)
    }

    /// One `tailscale ping` round trip (a disco ping: works where ACLs drop ICMP). Blocking.
    public func ping(_ ip: String) -> TailnetPing? {
        guard let r = try? runner.run(executable, ["ping", "-c", "1", "--timeout", "3s", ip], timeout: 8) else {
            return nil
        }
        return TailnetPing.parse(r.stdout + "\n" + r.stderr)
    }
}

// MARK: - probing an address

public enum TailnetError: Error, Equatable, CustomStringConvertible {
    case nothingAnswers(String)
    case notFound(String)

    /// English; the UI passes it through tr().
    public var description: String {
        switch self {
        case .nothingAnswers(let ip):
            return "Neither Cast, the remote nor ADB answers at \(ip). Is Tailscale running on the TV?"
        case .notFound(let name):
            return "Couldn't find “\(name)” on the tailnet or in DNS."
        }
    }
}

public enum TailnetProbe {
    /// Can a TCP connection to host:port be opened within `timeout`?
    public static func isOpen(_ host: String, port: UInt16, timeout: TimeInterval = 2.5) async -> Bool {
        await connectTime(host, port: port, timeout: timeout) != nil
    }

    /// Milliseconds to open a TCP connection, nil when it doesn't open in time.
    public static func connectTime(_ host: String, port: UInt16, timeout: TimeInterval = 2.5) async -> Double? {
        guard let p = NWEndpoint.Port(rawValue: port) else { return nil }
        let conn = NWConnection(host: NWEndpoint.Host(host), port: p, using: .tcp)
        let start = Date()
        let once = Once()
        return await withCheckedContinuation { (cont: CheckedContinuation<Double?, Never>) in
            let finish: @Sendable (Double?) -> Void = { v in
                if once.fire() { conn.cancel(); cont.resume(returning: v) }
            }
            conn.stateUpdateHandler = { state in
                switch state {
                case .ready: finish(Date().timeIntervalSince(start) * 1000)
                case .failed, .waiting, .cancelled: finish(nil)
                default: break
                }
            }
            conn.start(queue: .global(qos: .userInitiated))
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { finish(nil) }
        }
    }

    /// What a tailnet address offers. Any of Cast / remote / ADB answering is enough.
    public static func probe(ip: String, name: String?) async throws -> TailnetTV {
        var open: [UInt16: Bool] = [:]
        await withTaskGroup(of: (UInt16, Bool).self) { group in
            for port in Tailnet.tvPorts { group.addTask { (port, await isOpen(ip, port: port)) } }
            for await (port, ok) in group { open[port] = ok }
        }
        guard open.values.contains(true) else { throw TailnetError.nothingAnswers(ip) }
        var title = name ?? ip
        if open[Tailnet.castPort] == true, let friendly = await friendlyName(ip) { title = friendly }
        return TailnetTV(ip: ip, name: title, cast: open[Tailnet.castPort] == true,
                         remote: open[6466] == true, adb: open[5555] == true)
    }

    /// The TV's own name from its DIAL device description (port 8008).
    static func friendlyName(_ ip: String) async -> String? {
        guard let url = URL(string: "http://\(ip):8008/ssdp/device-desc.xml") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 4
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let xml = String(data: data, encoding: .utf8),
              let a = xml.range(of: "<friendlyName>"), let b = xml.range(of: "</friendlyName>", range: a.upperBound..<xml.endIndex)
        else { return nil }
        let name = xml[a.upperBound..<b.lowerBound].trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty ? nil : name
    }

    /// A tailnet IP, MagicDNS name or home address → (IPv4, peer host name if known). Blocking (DNS).
    public static func resolve(_ text: String, status: TailnetStatus?) throws -> (ip: String, host: String?) {
        let t = text.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        if Tailnet.isIPv4(t) { return (t, status?.peers.first { $0.ip == t }?.host) }
        if let p = status?.peer(matching: t) { return (p.ip, p.host) }
        var hints = addrinfo(ai_flags: 0, ai_family: AF_INET, ai_socktype: SOCK_STREAM, ai_protocol: 0,
                             ai_addrlen: 0, ai_canonname: nil, ai_addr: nil, ai_next: nil)
        var res: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(t, nil, &hints, &res) == 0, let first = res else { throw TailnetError.notFound(t) }
        defer { freeaddrinfo(res) }
        var buf = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
        let ok = first.pointee.ai_addr.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { sin -> Bool in
            var a = sin.pointee.sin_addr
            return inet_ntop(AF_INET, &a, &buf, socklen_t(INET_ADDRSTRLEN)) != nil
        }
        guard ok else { throw TailnetError.notFound(t) }
        return (String(cString: buf), nil)
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false
        func fire() -> Bool { lock.lock(); defer { lock.unlock() }; if done { return false }; done = true; return true }
    }
}

// MARK: - TVs added through Tailscale (remembered across launches)

/// A TV the user added over the tailnet, and what answered when it was added.
public struct TailnetTV: Codable, Equatable, Hashable, Sendable {
    public var ip: String
    public var name: String
    /// The Cast port answered: casting (catt) can reach it.
    public var cast: Bool
    public var remote: Bool
    public var adb: Bool

    public init(ip: String, name: String, cast: Bool, remote: Bool = true, adb: Bool = false) {
        self.ip = ip; self.name = name; self.cast = cast; self.remote = remote; self.adb = adb
    }

    public var device: DeviceInfo { DeviceInfo(ip: ip, name: name, model: "Tailscale") }
}

/// `@unchecked Sendable`: UserDefaults is thread-safe and the store keeps no other state.
public final class TailnetTVStore: @unchecked Sendable {
    public static let shared = TailnetTVStore()
    private let defaults: UserDefaults
    private let key = "tailnetTVs"

    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    public var all: [TailnetTV] {
        guard let data = defaults.data(forKey: key),
              let list = try? JSONDecoder().decode([TailnetTV].self, from: data) else { return [] }
        return list
    }

    public var devices: [DeviceInfo] { all.map(\.device) }

    public func entry(ip: String) -> TailnetTV? { all.first { $0.ip == ip } }

    public func save(_ tv: TailnetTV) {
        var list = all.filter { $0.ip != tv.ip }
        list.append(tv)
        write(list)
    }

    public func remove(ip: String) { write(all.filter { $0.ip != ip }) }

    /// The scan's TVs plus the saved tailnet ones it didn't find.
    public func merged(into found: [DeviceInfo]) -> [DeviceInfo] {
        let ips = Set(found.map(\.ip))
        return found + devices.filter { !ips.contains($0.ip) }
    }

    private func write(_ list: [TailnetTV]) {
        if let data = try? JSONEncoder().encode(list) { defaults.set(data, forKey: key) }
    }
}

// MARK: - link quality while a tailnet TV is selected

/// While a tailnet TV is selected (and someone is looking): ping it now and then, with
/// backoff while it's away. Gives the footer its "via Tailscale · 38 ms". Nothing listens
/// anywhere; this only measures Fling's own outgoing path.
@MainActor
public final class TailnetLinkMonitor: ObservableObject {
    public static let shared = TailnetLinkMonitor()

    public struct Link: Equatable, Sendable {
        public var online: Bool?
        public var ping: TailnetPing?
    }

    @Published public private(set) var ip: String?
    @Published public private(set) var link = Link()
    public var onPathBack: ((String) -> Void)?

    static let onlineEvery: Duration = .seconds(20)
    static let backoff: [Duration] = [.seconds(10), .seconds(20), .seconds(40), .seconds(80), .seconds(120)]
    private var task: Task<Void, Never>?
    private var cli: TailscaleCLI??

    public init() {}

    /// Follow `device` (nil or a LAN TV stops it).
    public func follow(_ device: DeviceInfo?) {
        let next = device?.isTailnet == true ? device?.ip : nil
        guard next != ip else { return }
        ip = next
        link = Link()
        task?.cancel()
        guard let next else { task = nil; return }
        task = Task { [weak self] in
            var misses = 0
            try? await Task.sleep(for: .milliseconds(600))
            while !Task.isCancelled {
                guard let self else { return }
                let cli: TailscaleCLI?
                if let cached = self.cli { cli = cached } else {
                    cli = await Task.detached { TailscaleCLI.find() }.value
                    self.cli = .some(cli)
                }
                var r = await Task.detached { cli?.ping(next) }.value
                if r == nil, let ms = await TailnetProbe.connectTime(next, port: 6466, timeout: 3) {
                    r = TailnetPing(ms: ms, direct: nil, relay: "")
                }
                guard !Task.isCancelled, self.ip == next else { return }
                let cameBack = r != nil && self.link.online == false
                self.link = Link(online: r != nil, ping: r)
                if cameBack { self.onPathBack?(next) }
                misses = r == nil ? misses + 1 : 0
                let wait = r != nil ? Self.onlineEvery : Self.backoff[min(misses - 1, Self.backoff.count - 1)]
                try? await Task.sleep(for: wait)
            }
        }
    }

    public func stop() { follow(nil) }
}
