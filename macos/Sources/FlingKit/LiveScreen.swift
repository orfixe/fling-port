// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

// Port: LIVE on the Mac — the TV's screen in its own window with the Mac's mouse and
// keyboard (scrcpy over ADB), the way the Linux build does it. Same options as the
// Windows build (desktop/fling_app.py, TvScreen): low-latency encoder options with a
// remembered plain fallback, quality presets incl. "Fast", the Mac pointer by default or
// the TV's own cursor, "mouse on the TV, no picture", and letters scrcpy can't type
// (Russian, accented) sent through the TV remote instead.

/// Picture quality: (longest side, frames/s, bit rate). 60 frames/s where it can: the TV's
/// hardware encoder holds a frame or two, so twice the frames is about half that wait.
public enum LiveQuality: String, CaseIterable, Sendable {
    case auto, sharp, fast, saver

    public var spec: (size: Int, fps: Int, bitRate: String) {
        switch self {
        case .auto:  return (1280, 60, "6M")
        case .sharp: return (1920, 30, "8M")
        case .fast:  return (960, 60, "4M")
        case .saver: return (960, 30, "2M")
        }
    }

    /// English UI title (goes through tr() in the app).
    public var title: String {
        switch self {
        case .auto:  return "Auto"
        case .sharp: return "Sharp"
        case .fast:  return "Fast"
        case .saver: return "Saver"
        }
    }

    public var detail: String {
        switch self {
        case .auto:  return "1280 px, 60 frames/s"
        case .sharp: return "1920 px, 30 frames/s — sharper, a bit more delay"
        case .fast:  return "960 px, 60 frames/s — the least delay, for games"
        case .saver: return "960 px, 2 Mbit/s — when the network is slow"
        }
    }
}

/// How the Mac's mouse acts on the picture.
public enum LiveMouse: String, CaseIterable, Sendable {
    /// The Mac's own pointer over the picture, each click exactly there (scrcpy --mouse=sdk).
    case mouse
    /// The TV's own cursor, moved by relative steps (scrcpy --mouse=uhid) — for apps that need it.
    case tvCursor
    /// Just watch: no mouse and no keys go to the TV.
    case watch

    public var title: String {
        switch self {
        case .mouse:    return "Mouse"
        case .tvCursor: return "TV cursor"
        case .watch:    return "Just watch"
        }
    }

    var scrcpyMouse: String { self == .tvCursor ? "uhid" : "sdk" }
}

public enum LiveMode: String, Sendable {
    /// The picture in its own window, mouse + keyboard to the TV.
    case screen
    /// "Mouse on the TV, no picture": a small window that captures the mouse for the TV's cursor.
    case mouseOnly
}

public struct LiveOptions: Equatable, Sendable {
    public var quality: LiveQuality = .auto
    public var mouse: LiveMouse = .mouse
    public var keyboardToTV = true
    public var sound = false

    public init(quality: LiveQuality = .auto, mouse: LiveMouse = .mouse,
                keyboardToTV: Bool = true, sound: Bool = false) {
        self.quality = quality; self.mouse = mouse
        self.keyboardToTV = keyboardToTV; self.sound = sound
    }
}

/// Why LIVE didn't open (or closed). English text; the app translates it.
public enum LiveError: Error, Equatable, Sendable {
    case noTV
    case noScrcpy
    case noADB
    case unauthorized
    case notConnected
    case noUHID
    case failedToStart(String)
    case closed(String)
}

public enum Scrcpy {
    /// Ask the TV's encoder for low latency: realtime priority and at most one frame held
    /// (MediaCodec KEY_PRIORITY / KEY_LATENCY).
    public static let lowLatencyCodecOptions = "priority:int=0,latency:int=1"
    /// An exit this soon after the start is the TV refusing something, not the user closing.
    public static let quickExit: TimeInterval = 15

    /// scrcpy's command line for one start. `mouse` overrides the option's mouse
    /// (the UHID → taps fallback).
    public static func arguments(serial: String, title: String, mode: LiveMode,
                                 options: LiveOptions, lowLatency: Bool,
                                 mouse: String? = nil) -> [String] {
        var args = ["-s", serial, "--window-title=\(title)"]
        switch mode {
        case .mouseOnly:
            args += ["--no-video", "--no-audio", "--mouse=\(mouse ?? "uhid")",
                     "--window-width=440", "--window-height=120"]
        case .screen:
            if options.mouse == .watch {
                args.append("--no-control")
            } else {
                args.append("--mouse=\(mouse ?? options.mouse.scrcpyMouse)")
                if !options.keyboardToTV { args.append("--keyboard=disabled") }
            }
            if !options.sound { args.append("--no-audio") }
            let q = options.quality.spec
            // no extra buffering anywhere: each frame is shown as soon as it's decoded
            args += ["--max-size=\(q.size)", "--max-fps=\(q.fps)",
                     "--video-bit-rate=\(q.bitRate)", "--video-buffer=0"]
            if lowLatency { args.append("--video-codec-options=\(lowLatencyCodecOptions)") }
        }
        return args
    }

    /// scrcpy's keyboard injects ASCII only: Russian letters (and the like) come back as
    /// "Could not inject char u+0444". The Android TV remote types any text.
    public static func uninjectableText(in chunk: String) -> String {
        let rx = try! NSRegularExpression(pattern: #"Could not inject char u\+([0-9a-fA-F]{4,6})"#)
        let ns = chunk as NSString
        var out = ""
        for m in rx.matches(in: chunk, range: NSRange(location: 0, length: ns.length)) {
            if let v = UInt32(ns.substring(with: m.range(at: 1)), radix: 16),
               let scalar = Unicode.Scalar(v) {
                out.unicodeScalars.append(scalar)
            }
        }
        return out
    }

    /// The TV's encoder won't take the low-latency options (it exits right away with a
    /// MediaCodec complaint): start it plain, and remember that TV.
    public static func encoderRefused(output: String, quick: Bool, usedLowLatency: Bool) -> Bool {
        guard quick, usedLowLatency else { return false }
        let t = output.lowercased()
        return t.contains("mediacodec") || t.contains("codecexception") || t.contains("illegalargument")
            || (t.contains("encoder") && t.contains("error"))
    }

    /// No UHID on this TV: the TV cursor can't work, taps can.
    public static func uhidMissing(output: String, quick: Bool, mouse: String) -> Bool {
        quick && mouse == "uhid" && output.lowercased().contains("uhid")
    }

    /// The last "ERROR: …" line scrcpy printed, if any.
    public static func lastError(in output: String) -> String? {
        output.split(whereSeparator: \.isNewline)
            .filter { $0.hasPrefix("ERROR:") }
            .last
            .map { $0.dropFirst("ERROR:".count).trimmingCharacters(in: .whitespaces) }
    }
}

/// Finds adb / scrcpy on a Mac where Fling runs from Finder (PATH is only /usr/bin:/bin).
/// Port note: the ADB work brings a FlingKit adb helper; this lookup is LIVE's own, small,
/// and can be folded into that one later.
public enum LiveTools {
    public static func find(_ name: String,
                            environment: [String: String] = ProcessInfo.processInfo.environment,
                            home: String = NSHomeDirectory(),
                            isExecutable: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) })
        -> String? {
        var candidates: [String] = []
        let override = name == "adb" ? (environment["FLING_ADB"] ?? environment["ADB"])
                                     : environment["FLING_\(name.uppercased())"]
        if let override, !override.isEmpty { candidates.append(override) }
        var dirs = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                    "\(home)/.local/bin", "\(home)/bin"]
        if name == "adb" {
            dirs += ["\(home)/Library/Android/sdk/platform-tools",
                     "/opt/homebrew/share/android-commandlinetools/platform-tools",
                     "/usr/local/share/android-commandlinetools/platform-tools"]
        }
        dirs += (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        candidates += dirs.map { "\($0)/\(name)" }
        return candidates.first(where: isExecutable)
    }

    public static let installScrcpy = "brew install scrcpy"
    public static let installADB = "brew install --cask android-platform-tools"
}

/// The `adb -s` serial for the TV at `ip`: one already connected, the wireless-debugging
/// port the TV announces (mDNS), or the classic 5555.
public struct LiveADB {
    public let adb: String
    public let runner: ProcessRunning

    public init(adb: String, runner: ProcessRunning = SystemProcessRunner()) {
        self.adb = adb; self.runner = runner
    }

    /// `adb devices` → (serial, state).
    public static func parseDevices(_ out: String) -> [(serial: String, state: String)] {
        out.split(whereSeparator: \.isNewline).compactMap { line in
            let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard parts.count >= 2, !line.hasPrefix("List of"), !line.hasPrefix("*") else { return nil }
            return (String(parts[0]), String(parts[1]))
        }
    }

    /// `adb mdns services` → the connect port each TV announces.
    public static func parseMDNS(_ out: String) -> [String: Int] {
        var ports: [String: Int] = [:]
        for line in out.split(whereSeparator: \.isNewline) where line.contains("_adb-tls-connect") {
            for word in line.split(whereSeparator: { $0 == " " || $0 == "\t" }) {
                let hp = word.split(separator: ":")
                if hp.count == 2, let port = Int(hp[1]), hp[0].split(separator: ".").count == 4 {
                    ports[String(hp[0])] = port
                }
            }
        }
        return ports
    }

    static func match(_ devices: [(serial: String, state: String)], ip: String)
        -> (serial: String, state: String)? {
        let mine = devices.filter { $0.serial.hasPrefix(ip + ":") }
        return mine.first(where: { $0.state == "device" }) ?? mine.first
    }

    private func adbRun(_ args: [String], timeout: TimeInterval = 8) -> String {
        guard let r = try? runner.run(adb, args, timeout: timeout) else { return "" }
        return r.stdout + r.stderr
    }

    /// Blocking (adb connect can take seconds): call it off the main actor.
    public func serial(for ip: String) throws -> String {
        func check() throws -> String? {
            guard let hit = Self.match(Self.parseDevices(adbRun(["devices"])), ip: ip) else { return nil }
            switch hit.state {
            case "device": return hit.serial
            case "unauthorized": throw LiveError.unauthorized
            default: return nil
            }
        }
        if let s = try check() { return s }
        var ports = [5555]
        if let announced = Self.parseMDNS(adbRun(["mdns", "services"], timeout: 4))[ip] {
            ports.insert(announced, at: 0)
        }
        for port in ports {
            _ = adbRun(["connect", "\(ip):\(port)"], timeout: 10)
            if let s = try check() { return s }
        }
        throw LiveError.notConnected
    }
}

/// What one scrcpy printed; touched on the main actor only.
final class RunLog: @unchecked Sendable {
    private(set) var text = ""
    func append(_ s: String) {
        text += s
        if text.count > 20_000 { text = String(text.suffix(10_000)) }   // --print-fps etc. keep coming
    }
}

/// The running scrcpy (one at a time) and what LIVE remembers.
@MainActor
public final class TVScreen: ObservableObject {
    @Published public private(set) var running: LiveMode?
    @Published public private(set) var busy = false
    @Published public var options: LiveOptions { didSet { save() } }

    /// Window titles (translated by the app).
    public var title: (LiveMode) -> String = { $0 == .screen ? "Fling — TV screen" : "Fling — TV cursor" }
    public var onError: ((LiveError) -> Void)?
    /// Text scrcpy couldn't type, for the TV remote.
    public var onUntypable: ((String) -> Void)?
    /// pid of a freshly started scrcpy: the app brings its window to the front.
    public var onStarted: ((Int32, LiveMode) -> Void)?

    private let defaults: UserDefaults
    private let runner: ProcessRunning
    private var process: Process?
    private var quiet = Set<ObjectIdentifier>()
    private var pending = ""

    private static let plainKey = "live.plain"

    public init(defaults: UserDefaults = .standard, runner: ProcessRunning = SystemProcessRunner()) {
        self.defaults = defaults
        self.runner = runner
        var o = LiveOptions()
        if let q = defaults.string(forKey: "live.quality").flatMap(LiveQuality.init) { o.quality = q }
        if let m = defaults.string(forKey: "live.mouse").flatMap(LiveMouse.init) { o.mouse = m }
        if defaults.object(forKey: "live.keyboard") != nil { o.keyboardToTV = defaults.bool(forKey: "live.keyboard") }
        o.sound = defaults.bool(forKey: "live.sound")
        options = o
    }

    private func save() {
        defaults.set(options.quality.rawValue, forKey: "live.quality")
        defaults.set(options.mouse.rawValue, forKey: "live.mouse")
        defaults.set(options.keyboardToTV, forKey: "live.keyboard")
        defaults.set(options.sound, forKey: "live.sound")
    }

    /// TVs whose encoder refused the low-latency options: they get the plain start.
    public func prefersPlain(_ ip: String) -> Bool {
        (defaults.stringArray(forKey: Self.plainKey) ?? []).contains(ip)
    }

    private func rememberPlain(_ ip: String) {
        var list = defaults.stringArray(forKey: Self.plainKey) ?? []
        if !list.contains(ip) { list.append(ip); defaults.set(list, forKey: Self.plainKey) }
    }

    /// The same menu item again closes it; the other mode replaces it.
    public func toggle(_ mode: LiveMode, ip: String?) {
        if running == mode { stop(); return }
        open(mode, ip: ip)
    }

    /// scrcpy takes its options only at start: changing one restarts a running picture.
    public func setOptions(_ new: LiveOptions, ip: String?) {
        guard new != options else { return }
        options = new
        if running == .screen { open(.screen, ip: ip) }
    }

    public func open(_ mode: LiveMode, ip: String?) {
        guard let ip, !ip.isEmpty else { onError?(.noTV); return }
        guard !busy else { return }
        guard let exe = LiveTools.find("scrcpy") else { onError?(.noScrcpy); return }
        guard let adb = LiveTools.find("adb") else { onError?(.noADB); return }
        busy = true
        let runner = self.runner
        Task.detached {
            let result = Result { try LiveADB(adb: adb, runner: runner).serial(for: ip) }
            await MainActor.run {
                self.busy = false
                switch result {
                case .success(let serial):
                    self.stop()
                    self.start(exe: exe, adb: adb, ip: ip, serial: serial, mode: mode, mouse: nil)
                case .failure(let e):
                    self.onError?(e as? LiveError ?? .notConnected)
                }
            }
        }
    }

    private func start(exe: String, adb: String, ip: String, serial: String,
                       mode: LiveMode, mouse: String?) {
        let low = mode == .screen && !prefersPlain(ip)
        let args = Scrcpy.arguments(serial: serial, title: title(mode), mode: mode,
                                    options: options, lowLatency: low, mouse: mouse)
        let usedMouse = mouse ?? (mode == .mouseOnly ? "uhid" : options.mouse.scrcpyMouse)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["ADB"] = adb      // one adb server: the one Fling found, not whatever is on PATH
        let dirs = [(adb as NSString).deletingLastPathComponent, (exe as NSString).deletingLastPathComponent,
                    "/opt/homebrew/bin", "/usr/local/bin"]
        env["PATH"] = (dirs + [env["PATH"] ?? "/usr/bin:/bin"]).joined(separator: ":")
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        p.standardInput = FileHandle.nullDevice
        let log = RunLog()
        let started = Date()
        let id = ObjectIdentifier(p)

        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty else { h.readabilityHandler = nil; return }
            let chunk = String(decoding: data, as: UTF8.self)
            Task { @MainActor in
                guard let self else { return }
                log.append(chunk)
                guard mode == .screen else { return }
                let text = Scrcpy.uninjectableText(in: chunk)
                guard !text.isEmpty else { return }
                self.pending += text
                // a short wait gathers a typed word into one remote message
                Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: 120_000_000)
                    guard let self, !self.pending.isEmpty else { return }
                    let s = self.pending
                    self.pending = ""
                    self.onUntypable?(s)
                }
            }
        }
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor in
                guard let self else { return }
                // let the last output in before deciding why it closed
                try? await Task.sleep(nanoseconds: 150_000_000)
                pipe.fileHandleForReading.readabilityHandler = nil
                if self.process === proc { self.process = nil; self.running = nil }
                if self.quiet.remove(id) != nil || code == 0 { return }
                let output = log.text
                let quick = Date().timeIntervalSince(started) < Scrcpy.quickExit
                if Scrcpy.encoderRefused(output: output, quick: quick, usedLowLatency: low) {
                    self.rememberPlain(ip)
                    self.start(exe: exe, adb: adb, ip: ip, serial: serial, mode: mode, mouse: mouse)
                    return
                }
                if Scrcpy.uhidMissing(output: output, quick: quick, mouse: usedMouse) {
                    if mode == .screen {   // no UHID on this TV: taps instead of a cursor
                        self.start(exe: exe, adb: adb, ip: ip, serial: serial, mode: mode, mouse: "sdk")
                    } else {
                        self.onError?(.noUHID)
                    }
                    return
                }
                self.onError?(.closed(Scrcpy.lastError(in: output) ?? "scrcpy \(code)"))
            }
        }
        do {
            try p.run()
        } catch {
            pipe.fileHandleForReading.readabilityHandler = nil
            onError?(.failedToStart(error.localizedDescription))
            return
        }
        process = p
        running = mode
        onStarted?(p.processIdentifier, mode)
    }

    /// Close the window; scrcpy cleans up on the TV when it can. Killed if it hangs.
    public func stop() {
        guard let p = process, p.isRunning else { process = nil; running = nil; return }
        quiet.insert(ObjectIdentifier(p))
        p.terminate()
        process = nil
        running = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    /// On quit: only Fling's own scrcpy, stopped before Fling is gone (up to ~1.5 s).
    public func stopForQuit() {
        guard let p = process, p.isRunning else { return }
        quiet.insert(ObjectIdentifier(p))
        p.terminate()
        let deadline = Date().addingTimeInterval(1.5)
        while p.isRunning && Date() < deadline { usleep(50_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        process = nil
        running = nil
    }
}
