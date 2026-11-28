// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import ImageIO

/// Port: where adb lives on a Mac. GUI apps don't get the shell's PATH, so the
/// usual install places are probed first, then PATH.
public enum ADBLocator {
    /// Shown when nothing is found; Fling never installs adb by itself.
    public static let installCommand = "brew install android-platform-tools"

    public static func candidates(home: String = NSHomeDirectory(),
                                  env: [String: String] = ProcessInfo.processInfo.environment) -> [String] {
        var list = [
            "/opt/homebrew/bin/adb",
            "/usr/local/bin/adb",
            "\(home)/Library/Android/sdk/platform-tools/adb",
        ]
        for key in ["ANDROID_HOME", "ANDROID_SDK_ROOT"] {
            if let sdk = env[key], !sdk.isEmpty { list.append("\(sdk)/platform-tools/adb") }
        }
        list.append("\(home)/Library/Application Support/Fling Port/platform-tools/adb")
        for dir in (env["PATH"] ?? "").split(separator: ":") where !dir.isEmpty {
            list.append("\(dir)/adb")
        }
        var seen = Set<String>()
        return list.filter { seen.insert($0).inserted }
    }

    public static func find(isExecutable: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) },
                            home: String = NSHomeDirectory(),
                            env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
        candidates(home: home, env: env).first(where: isExecutable)
    }
}

/// Port: each TV keeps the ADB port that last worked for it (one global port
/// mixed up two TVs on Windows before 0.5.4).
public protocol ADBPortStore: AnyObject {
    func port(for ip: String) -> Int?
    func setPort(_ port: Int, for ip: String)
}

public final class UserDefaultsADBPortStore: ADBPortStore {
    private let key = "adb.ports"
    private let defaults: UserDefaults
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    public func port(for ip: String) -> Int? {
        (defaults.dictionary(forKey: key) as? [String: Int])?[ip]
    }
    public func setPort(_ port: Int, for ip: String) {
        var d = (defaults.dictionary(forKey: key) as? [String: Int]) ?? [:]
        d[ip] = port
        defaults.set(d, forKey: key)
    }
}

public final class MemoryADBPortStore: ADBPortStore {
    public var ports: [String: Int] = [:]
    public init() {}
    public func port(for ip: String) -> Int? { ports[ip] }
    public func setPort(_ port: Int, for ip: String) { ports[ip] = port }
}

/// Result of trying to reach a TV over ADB.
public enum ADBReach: Equatable, Sendable {
    case device(port: Int)
    case unauthorized(port: Int)
    /// Something answered but adb lists it as offline / unknown.
    case offline
    /// No ADB port answered: debugging is off (or the TV is away).
    case closed
}

/// Thin, blocking wrapper over the adb command line. Every call goes to
/// Fling's own adb server on its own port (`-P`), so quitting Fling stops
/// only that server — never Android Studio's on 5037.
public final class ADBClient: @unchecked Sendable {
    public static let serverPort = 15037

    public let executable: String
    private let runner: ProcessRunning
    private let store: ADBPortStore
    /// TCP probe; injected in tests so they never touch the network.
    private let probe: (String, Int) -> Bool
    private let lock = NSLock()
    private var _used = false

    public init(executable: String, runner: ProcessRunning = SystemProcessRunner(),
                store: ADBPortStore = UserDefaultsADBPortStore(),
                probe: @escaping (String, Int) -> Bool = ADBClient.tcpOpen) {
        self.executable = executable
        self.runner = runner
        self.store = store
        self.probe = probe
    }

    /// True once Fling has started (or talked to) its adb server.
    public var used: Bool { lock.lock(); defer { lock.unlock() }; return _used }

    @discardableResult
    public func adb(_ args: [String], timeout: TimeInterval = 20) throws -> ProcessResult {
        lock.lock(); _used = true; lock.unlock()
        return try runner.run(executable, ["-P", String(Self.serverPort)] + args, timeout: timeout)
    }

    public func savedPort(_ ip: String) -> Int? { store.port(for: ip) }

    public func serial(_ ip: String, port: Int? = nil) -> String {
        "\(ip):\(port ?? store.port(for: ip) ?? 5555)"
    }

    // MARK: - discovery / connect

    public func mdns() -> [String: ADBAnnounce] {
        guard let r = try? adb(["mdns", "services"], timeout: 6) else { return [:] }
        return ADBParse.mdnsServices(r.stdout)
    }

    public func devicesOutput() -> String {
        (try? adb(["devices"], timeout: 5))?.stdout ?? ""
    }

    /// The ports worth trying, in order: typed, remembered, announced, 5555.
    public func candidatePorts(ip: String, typed: Int?, announce: ADBAnnounce) -> [Int] {
        var ports: [Int] = []
        if let typed { ports.append(typed) }
        if let saved = store.port(for: ip) { ports.append(saved) }
        if let c = announce.connect { ports.append(c) }
        if let a = announce.adb { ports.append(a) }
        ports.append(5555)
        var seen = Set<Int>()
        return ports.filter { seen.insert($0).inserted }
    }

    /// `adb connect` on the first port that answers, then what `adb devices`
    /// says about it. A port that ends up as "device" is remembered for this TV.
    public func reach(ip: String, typed: Int? = nil, announce: ADBAnnounce) -> ADBReach {
        var last: ADBReach = .closed
        for port in candidatePorts(ip: ip, typed: typed, announce: announce) {
            guard probe(ip, port) else { continue }
            let serial = "\(ip):\(port)"
            _ = try? adb(["connect", serial], timeout: 8)
            let states = ADBParse.devices(devicesOutput())
            switch states[serial] {
            case "device":
                store.setPort(port, for: ip)
                return .device(port: port)
            case "unauthorized":
                return .unauthorized(port: port)
            default:
                last = .offline
            }
        }
        return last
    }

    /// While the TV shows "Allow USB debugging?": has it accepted us yet?
    public func stateNow(ip: String) -> (state: String, port: Int?)? {
        ADBParse.deviceState(devicesOutput(), ip: ip)
    }

    public func remember(port: Int, for ip: String) { store.setPort(port, for: ip) }

    /// `adb pair ip:port code`; true on "Successfully paired".
    public func pair(ip: String, port: Int, code: String) -> (ok: Bool, output: String) {
        guard let r = try? adb(["pair", "\(ip):\(port)", code], timeout: 20) else {
            return (false, "adb didn't start")
        }
        let out = (r.stdout + "\n" + r.stderr).trimmingCharacters(in: .whitespacesAndNewlines)
        return (ADBParse.pairSucceeded(out), out)
    }

    public func disconnect(ip: String) {
        _ = try? adb(["disconnect", serial(ip)], timeout: 8)
    }

    // MARK: - on the TV

    public func shell(_ ip: String, _ command: String, timeout: TimeInterval = 8) throws -> ProcessResult {
        try adb(["-s", serial(ip), "shell", command], timeout: timeout)
    }

    public static let nowPlayingCommand = "dumpsys media_session; echo __UP__; cat /proc/uptime"

    /// nil when the TV didn't answer over ADB (not connected / refused).
    public func nowPlaying(ip: String) -> ADBNowPlaying? {
        guard let r = try? shell(ip, Self.nowPlayingCommand, timeout: 6),
              r.succeeded else { return nil }
        return ADBParse.nowPlaying(r.stdout)
    }

    /// One quiet reconnect for a TV that worked before; returns its state.
    public func reconnect(ip: String) -> String? {
        let s = serial(ip)
        _ = try? adb(["connect", s], timeout: 6)
        return ADBParse.devices(devicesOutput())[s]
    }

    public enum ShotError: Error, Equatable { case failed(String) }

    /// Screenshot of the TV to `folder`/tv-YYYYMMDD-HHMMSS.png. Text-only
    /// pipes, so the PNG is made on the TV and pulled as a file.
    public func screenshot(ip: String, folder: URL, now: Date = Date()) throws -> URL {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyyMMdd-HHmmss"
        let dest = folder.appendingPathComponent("tv-\(f.string(from: now)).png")
        let remote = "/data/local/tmp/fling-shot.png"
        let cap = try shell(ip, "screencap -p \(remote)", timeout: 20)
        guard cap.succeeded else {
            throw ShotError.failed(ADBParse.lastLine(cap.stderr + cap.stdout) ?? "no data")
        }
        let pull = try adb(["-s", serial(ip), "pull", remote, dest.path], timeout: 30)
        _ = try? shell(ip, "rm -f \(remote)", timeout: 5)
        guard pull.succeeded, FileManager.default.fileExists(atPath: dest.path) else {
            throw ShotError.failed(ADBParse.lastLine(pull.stderr + pull.stdout) ?? "no data")
        }
        return dest
    }

    /// nil on success, else a plain-words reason. Several files = one split app.
    public func install(ip: String, apks: [String]) -> String? {
        let cmd = apks.count > 1 ? ["install-multiple", "-r"] : ["install", "-r"]
        guard let r = try? adb(["-s", serial(ip)] + cmd + apks, timeout: 600) else {
            return "adb didn't start"
        }
        if r.timedOut { return "The TV didn't answer in 10 minutes." }
        return ADBParse.installFailure(exitCode: r.exitCode, output: r.stdout + "\n" + r.stderr)
    }

    /// Stops Fling's own adb server (its own port) — never someone else's.
    public func stopOwnServer() {
        guard used else { return }
        _ = try? runner.run(executable, ["-P", String(Self.serverPort), "kill-server"], timeout: 5)
    }

    // MARK: - helpers

    /// A black picture: DRM-protected video (YouTube / Netflix) can't be captured.
    public static func looksBlack(_ url: URL) -> Bool {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let thumb = CGImageSourceCreateThumbnailAtIndex(src, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceThumbnailMaxPixelSize: 48,
              ] as CFDictionary) else { return false }
        let w = thumb.width, h = thumb.height
        var px = [UInt8](repeating: 0, count: w * h * 4)
        guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
        ctx.draw(thumb, in: CGRect(x: 0, y: 0, width: w, height: h))
        var brightest = 0
        for i in stride(from: 0, to: px.count, by: 4) {
            brightest = max(brightest, Int(px[i]), Int(px[i + 1]), Int(px[i + 2]))
        }
        return brightest < 8
    }

    /// Does anything accept TCP on ip:port within 1.5 s?
    public static func tcpOpen(_ ip: String, _ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port).bigEndian)
        guard inet_pton(AF_INET, ip, &addr.sin_addr) == 1 else { return false }
        let flags = fcntl(fd, F_GETFL, 0)
        _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
        var noSigPipe: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size))
        let rc = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if rc == 0 { return true }
        guard errno == EINPROGRESS else { return false }
        var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
        guard poll(&pfd, 1, 1500) == 1 else { return false }
        var err: Int32 = 0
        var len = socklen_t(MemoryLayout<Int32>.size)
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len)
        return err == 0
    }
}
