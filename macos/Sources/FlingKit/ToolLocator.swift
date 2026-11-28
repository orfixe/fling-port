// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: the Tools window's detection half (desktop/fling_tools.py) for macOS — the
// outside programs Fling uses (catt, adb, scrcpy): every copy already on the Mac with
// its version, the newest one the publisher offers, and how to install / update it
// (Homebrew when it is there, uv for catt as macos/install.sh does).
import Foundation

public enum ToolVersion {
    /// "Version 35.0.2-12147458" → [35, 0, 2, 12147458] (the first four numbers).
    public static func parse(_ v: String?) -> [Int] {
        guard let v else { return [] }
        var out: [Int] = [], cur = ""
        for ch in v {
            if ch.isASCII, ch.isNumber { cur.append(ch) }
            else if !cur.isEmpty { out.append(Int(cur) ?? 0); cur = "" }
            if out.count == 4 { break }
        }
        if !cur.isEmpty, out.count < 4 { out.append(Int(cur) ?? 0) }
        return out
    }

    /// Tuple order, like Python: [1, 2] < [1, 2, 0] < [1, 3].
    public static func less(_ a: [Int], _ b: [Int]) -> Bool {
        for (x, y) in zip(a, b) where x != y { return x < y }
        return a.count < b.count
    }

    public static func isNewer(_ latest: String?, than have: String?) -> Bool {
        let l = parse(latest), h = parse(have)
        return !l.isEmpty && !h.isEmpty && less(h, l)
    }

    /// The version in a program's `--version` output, by the tool's pattern.
    public static func extract(_ output: String, pattern: String) -> String? {
        guard let rx = try? NSRegularExpression(pattern: pattern, options: [.anchorsMatchLines]),
              let m = rx.firstMatch(in: output, range: NSRange(output.startIndex..., in: output)),
              m.numberOfRanges > 1, let r = Range(m.range(at: 1), in: output) else { return nil }
        return String(output[r])
    }
}

public struct ToolSpec: Sendable, Identifiable {
    public let key: String
    public let title: String
    public let purpose: String            // English; the UI translates it
    public let license: String
    public let licenseURL: String
    public let homepage: String
    public let exe: String
    public let versionArgs: [String]
    public let versionPattern: String
    public let minVersion: String         // older copies are listed but not picked
    public let brewName: String           // Homebrew formula / cask
    public let brewCask: Bool
    public let manual: String             // English: what to do without Homebrew
    public var id: String { key }
}

public struct ToolCopy: Equatable, Sendable, Identifiable {
    public let path: String
    public let source: String             // brew | uv | sdk | path | local
    public let version: String?
    public let compatible: Bool
    public var id: String { path }
}

/// What a click on Install / Update runs, shown in plain words before it starts.
public struct ToolCommand: Equatable, Sendable {
    public let executable: String
    public let arguments: [String]
    public var display: String {
        ([executable.components(separatedBy: "/").last ?? executable] + arguments).joined(separator: " ")
    }
}

public enum ToolLocator {
    public static let tools: [ToolSpec] = [
        ToolSpec(key: "catt", title: "catt",
                 purpose: "Sending videos and links to the TV over Cast.",
                 license: "BSD-2-Clause", licenseURL: "https://github.com/skorokithakis/catt/blob/master/LICENSE",
                 homepage: "https://github.com/skorokithakis/catt",
                 exe: "catt", versionArgs: ["--version"], versionPattern: #"catt\s+v?(\d+(?:\.\d+)+)"#,
                 minVersion: "", brewName: "", brewCask: false,
                 manual: "Install uv (https://docs.astral.sh/uv/), then run in Terminal: uv tool install catt"),
        ToolSpec(key: "adb", title: "Android platform-tools (adb)",
                 purpose: "ADB: what's playing on the TV, screenshots, installing APKs and the ADB console.",
                 license: "Android SDK License", licenseURL: "https://developer.android.com/studio/terms",
                 homepage: "https://developer.android.com/tools/releases/platform-tools",
                 exe: "adb", versionArgs: ["version"], versionPattern: #"^Version\s+(\d+(?:\.\d+)+)"#,
                 minVersion: "30.0.0", brewName: "android-platform-tools", brewCask: true,
                 manual: "Download “SDK Platform-Tools for Mac” from Google, unzip it and put the platform-tools folder in your home folder, or install Homebrew (https://brew.sh) and press Install here."),
        ToolSpec(key: "scrcpy", title: "scrcpy",
                 purpose: "The TV screen in a Fling window and the computer's mouse on the TV (LIVE).",
                 license: "Apache-2.0", licenseURL: "https://github.com/Genymobile/scrcpy/blob/master/LICENSE",
                 homepage: "https://github.com/Genymobile/scrcpy",
                 exe: "scrcpy", versionArgs: ["--version"], versionPattern: #"scrcpy\s+v?(\d+(?:\.\d+)+)"#,
                 minVersion: "2.4", brewName: "scrcpy", brewCask: false,
                 manual: "Install Homebrew (https://brew.sh) and press Install here, or download the macOS build from the scrcpy releases page."),
    ]

    public static func spec(_ key: String) -> ToolSpec? { tools.first { $0.key == key } }

    static var home: String { FileManager.default.homeDirectoryForCurrentUser.path }

    /// GUI apps don't get the shell's PATH: the usual install places, then PATH.
    public static var searchPath: [String] {
        var dirs = ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin"]
        dirs += (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").map(String.init)
        dirs += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        var seen = Set<String>()
        return dirs.filter { !$0.isEmpty && seen.insert($0).inserted }
    }

    /// Environment for brew / uv runs: a PATH that has them, no brew hints.
    public static var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = searchPath.joined(separator: ":")
        env["HOMEBREW_NO_ENV_HINTS"] = "1"
        env["HOMEBREW_NO_INSTALL_CLEANUP"] = "1"
        return env
    }

    public static func which(_ exe: String, in dirs: [String] = searchPath) -> String? {
        for d in dirs {
            let p = (d as NSString).appendingPathComponent(exe)
            if FileManager.default.isExecutableFile(atPath: p) { return p }
        }
        return nil
    }

    public static var brew: String? {
        ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"].first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    public static var uv: String? { which("uv") }

    /// Candidate paths of a tool, in search order.
    public static func places(_ spec: ToolSpec) -> [String] {
        var out = searchPath.map { ($0 as NSString).appendingPathComponent(spec.exe) }
        if spec.key == "adb" {
            let env = ProcessInfo.processInfo.environment
            for root in [env["ANDROID_HOME"], env["ANDROID_SDK_ROOT"], "\(home)/Library/Android/sdk",
                         "\(home)/platform-tools/..", "\(home)/Downloads/platform-tools/.."].compactMap({ $0 }) {
                out.append(((root as NSString).appendingPathComponent("platform-tools/adb") as NSString).standardizingPath)
            }
        }
        if spec.key == "scrcpy" {
            out.append("\(home)/scrcpy/scrcpy")
        }
        return out
    }

    /// Where a copy came from, for the label next to it.
    public static func source(of path: String) -> String {
        let real = (path as NSString).resolvingSymlinksInPath
        if real.contains("/Cellar/") || real.contains("/Caskroom/") || real.hasPrefix("/opt/homebrew/") { return "brew" }
        if real.contains("/uv/tools/") || path.hasPrefix("\(home)/.local/bin/") { return "uv" }
        if path.contains("/Library/Android/sdk/") || path.contains("/platform-tools/") { return "sdk" }
        return "path"
    }

    /// The version a copy reports (runs it; call off the main thread).
    public static func version(of path: String, spec: ToolSpec,
                               runner: ProcessRunning = SystemProcessRunner()) -> String? {
        guard let r = try? runner.run(path, spec.versionArgs, timeout: 8) else { return nil }
        return ToolVersion.extract(r.stdout + "\n" + r.stderr, pattern: spec.versionPattern)
    }

    public static func compatible(_ version: String?, spec: ToolSpec) -> Bool {
        guard !spec.minVersion.isEmpty, let version else { return true }
        return !ToolVersion.less(ToolVersion.parse(version), ToolVersion.parse(spec.minVersion))
    }

    /// Every copy on this Mac, best first (compatible, newest). Runs each one.
    public static func scan(_ spec: ToolSpec, runner: ProcessRunning = SystemProcessRunner()) -> [ToolCopy] {
        var seen = Set<String>(), copies: [ToolCopy] = []
        for p in places(spec) where FileManager.default.isExecutableFile(atPath: p) {
            let real = (p as NSString).resolvingSymlinksInPath
            guard seen.insert(real).inserted else { continue }
            let v = version(of: p, spec: spec, runner: runner)
            copies.append(ToolCopy(path: p, source: source(of: p), version: v, compatible: compatible(v, spec: spec)))
        }
        return sorted(copies)
    }

    static func sorted(_ copies: [ToolCopy]) -> [ToolCopy] {
        copies.enumerated().sorted { a, b in
            if a.element.compatible != b.element.compatible { return a.element.compatible }
            let va = ToolVersion.parse(a.element.version), vb = ToolVersion.parse(b.element.version)
            if va != vb { return ToolVersion.less(vb, va) }
            return a.offset < b.offset
        }.map(\.element)
    }

    /// The copy Fling uses: the best compatible one. Other modules (ADB, LIVE) can call this.
    public static func find(_ key: String) -> String? {
        guard let spec = spec(key) else { return nil }
        return scan(spec).first { $0.compatible }?.path
    }

    // MARK: - install / update

    /// The command a click on Install (or Update, when `have` is the copy in use) runs;
    /// nil when there's nothing to run here and the manual text applies.
    public static func command(for spec: ToolSpec, have: ToolCopy?, brew: String?, uv: String?) -> ToolCommand? {
        let updating = have != nil
        if spec.key == "catt" {
            if updating, have?.source == "brew", let brew { return ToolCommand(executable: brew, arguments: ["upgrade", "catt"]) }
            if let uv { return ToolCommand(executable: uv, arguments: ["tool", updating ? "upgrade" : "install", "catt"]) }
            // as macos/install.sh does: uv from astral.sh (or Homebrew), then catt with it
            let getUV = brew.map { "\"\($0)\" install uv && UV=\"$(dirname \"\($0)\")/uv\"" }
                ?? "t=\"$(mktemp -d)\" && curl -fsSL https://astral.sh/uv/install.sh -o \"$t/uv-install.sh\" && sh \"$t/uv-install.sh\" && UV=\"$HOME/.local/bin/uv\""
            return ToolCommand(executable: "/bin/sh", arguments: ["-c", "set -e; \(getUV); \"$UV\" tool install catt"])
        }
        guard let brew, !spec.brewName.isEmpty else { return nil }
        // a copy that isn't Homebrew's can't be updated by it: offer installing Homebrew's
        if updating, have?.source != "brew" { return nil }
        var args = [updating ? "upgrade" : "install"]
        if spec.brewCask { args.append("--cask") }
        args.append(spec.brewName)
        return ToolCommand(executable: brew, arguments: args)
    }

    // MARK: - latest versions (display)

    /// Google's SDK repository: the newest stable platform-tools revision.
    public static func parsePlatformToolsVersion(_ xml: Data) -> String? {
        final class P: NSObject, XMLParserDelegate {
            var inPkg = false, stable = true, inRev = false, field = "", text = ""
            var rev: [String: String] = [:], found: [[Int]] = []
            func parser(_ p: XMLParser, didStartElement e: String, namespaceURI: String?, qualifiedName: String?,
                        attributes a: [String: String]) {
                let name = e.split(separator: ":").last.map(String.init) ?? e
                if name == "remotePackage" { inPkg = a["path"] == "platform-tools"; stable = true; rev = [:] }
                guard inPkg else { return }
                if name == "channelRef", let r = a["ref"], r != "channel-0" { stable = false }
                if name == "revision" { inRev = true }
                if inRev, ["major", "minor", "micro"].contains(name) { field = name; text = "" }
            }
            func parser(_ p: XMLParser, foundCharacters s: String) { if !field.isEmpty { text += s } }
            func parser(_ p: XMLParser, didEndElement e: String, namespaceURI: String?, qualifiedName: String?) {
                let name = e.split(separator: ":").last.map(String.init) ?? e
                if !field.isEmpty, name == field { rev[field] = text.trimmingCharacters(in: .whitespacesAndNewlines); field = "" }
                if name == "revision" { inRev = false }
                if name == "remotePackage" {
                    if inPkg, stable { found.append(["major", "minor", "micro"].map { Int(rev[$0] ?? "0") ?? 0 }) }
                    inPkg = false
                }
            }
        }
        let p = P(), parser = XMLParser(data: xml)
        parser.delegate = p
        parser.parse()
        guard let best = p.found.max(by: ToolVersion.less) else { return nil }
        return best.map(String.init).joined(separator: ".")
    }

    /// The newest version the publisher offers (network; nil when unknown).
    public static func latestVersion(_ spec: ToolSpec, http: CatalogHTTP) async -> String? {
        switch spec.key {
        case "catt":
            guard let d = try? await http.get("https://pypi.org/pypi/catt/json", limit: 4 << 20, strict: false),
                  let o = try? jsonObject(d) else { return nil }
            return (o["info"] as? [String: Any])?["version"] as? String
        case "adb":
            guard let d = try? await http.get("https://dl.google.com/android/repository/repository2-3.xml",
                                              limit: 16 << 20, strict: false) else { return nil }
            return parsePlatformToolsVersion(d)
        case "scrcpy":
            guard let d = try? await http.get("https://api.github.com/repos/Genymobile/scrcpy/releases/latest",
                                              limit: 8 << 20, strict: false),
                  let o = try? jsonObject(d), var tag = o["tag_name"] as? String else { return nil }
            if tag.hasPrefix("v") { tag.removeFirst() }
            return tag
        default:
            return nil
        }
    }
}

/// Runs an install / update command and streams its output line by line.
public final class StreamingProcess: @unchecked Sendable {
    private let process = Process()

    public init() {}

    /// Runs to the end; `onOutput` gets each chunk of stdout + stderr (any thread).
    public func run(_ cmd: ToolCommand, onOutput: @escaping @Sendable (String) -> Void) async -> Int32 {
        process.executableURL = URL(fileURLWithPath: cmd.executable)
        process.arguments = cmd.arguments
        process.environment = ToolLocator.environment
        process.standardInput = FileHandle.nullDevice
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            onOutput(String(decoding: d, as: UTF8.self))
        }
        return await withCheckedContinuation { (c: CheckedContinuation<Int32, Never>) in
            process.terminationHandler = { p in
                // let the last output through before reporting the end
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) {
                    pipe.fileHandleForReading.readabilityHandler = nil
                    c.resume(returning: p.terminationStatus)
                }
            }
            do { try process.run() } catch {
                onOutput("\(error.localizedDescription)\n")
                process.terminationHandler = nil
                c.resume(returning: -1)
            }
        }
    }

    public func cancel() { if process.isRunning { process.terminate() } }
}
