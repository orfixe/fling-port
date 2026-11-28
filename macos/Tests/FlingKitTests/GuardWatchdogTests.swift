// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
import Foundation
import Testing
@testable import FlingKit

/// Port (macOS guard): the CPU rule, the process tree, the child check, the self check.
extension GuardSuite {
@Suite final class GuardWatchdogTests {

    // MARK: CPU rule (no processes)

    private let a = ProcessKey(pid: 100, start: 1)
    private let ns: UInt64 = 1_000_000_000

    /// `share` of a 4-core machine, sampled every 5 s for `seconds`; returns when it was stopped.
    private func run(share: Double, seconds: Int, logic: inout CPUWatchdogLogic) -> Int? {
        var cpu: UInt64 = 0
        for t in stride(from: 0, through: seconds, by: 5) {
            if !logic.observe([a: cpu], now: TimeInterval(t), cpuCount: 4).isEmpty { return t }
            cpu += UInt64(share * 4 * 5 * Double(ns))
        }
        return nil
    }

    @Test func test_over_30_percent_for_90_seconds_is_stopped() {
        var logic = CPUWatchdogLogic(selfPID: 1)
        #expect(run(share: 0.5, seconds: 200, logic: &logic) == 90)
    }

    @Test func test_under_the_limit_or_shorter_is_not_stopped() {
        var l1 = CPUWatchdogLogic(selfPID: 1)
        #expect(run(share: 0.25, seconds: 600, logic: &l1) == nil)
        var l2 = CPUWatchdogLogic(selfPID: 1)
        #expect(run(share: 0.9, seconds: 85, logic: &l2) == nil)
    }

    @Test func test_a_quiet_moment_restarts_the_window() {
        var logic = CPUWatchdogLogic(selfPID: 1)
        var cpu: UInt64 = 0
        var stoppedAt: Int?
        for t in stride(from: 0, through: 300, by: 5) {
            if !logic.observe([a: cpu], now: TimeInterval(t), cpuCount: 4).isEmpty { stoppedAt = t; break }
            let busy = t != 60       // idle for one interval at 60–65 s
            cpu += busy ? UInt64(0.8 * 4 * 5 * Double(ns)) : 0
        }
        #expect(stoppedAt == 65 + 90)
    }

    @Test func test_fling_itself_is_never_stopped_and_gone_processes_are_forgotten() {
        var logic = CPUWatchdogLogic(selfPID: 100)
        #expect(run(share: 1.0, seconds: 300, logic: &logic) == nil)
        #expect(logic.tracks.isEmpty)
        var l2 = CPUWatchdogLogic(selfPID: 1)
        _ = l2.observe([a: 0], now: 0, cpuCount: 1)
        _ = l2.observe([:], now: 5, cpuCount: 1)
        #expect(l2.tracks.isEmpty)
        // a new process with the same pid starts from zero
        let reused = ProcessKey(pid: 100, start: 2)
        #expect(l2.observe([reused: 50 * ns], now: 10, cpuCount: 1).isEmpty)
    }

    // MARK: real processes

    private func spawn(_ path: String, _ args: [String]) throws -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        try p.run()
        return p
    }

    @Test func test_process_tree_sees_children_and_their_cpu() throws {
        let sleeper = try spawn("/bin/sleep", ["30"])
        let busy = try spawn("/usr/bin/yes", [])
        defer { sleeper.terminate(); busy.terminate() }
        Thread.sleep(forTimeInterval: 1.0)
        let nodes = ProcessTree.descendants()
        #expect(nodes.contains { $0.key.pid == sleeper.processIdentifier })
        #expect(ProcessTree.path(of: sleeper.processIdentifier) == "/bin/sleep")
        let cpu = try #require(ProcessTree.cpuNanos(busy.processIdentifier))
        // ~1 s of one core, in nanoseconds (Mach units converted)
        #expect(cpu > 300_000_000)
        #expect(cpu < 3_000_000_000)
        #expect(ProcessTree.startTime(getpid()) != nil)
    }

    private func tempDir() throws -> URL {
        let d = FileManager.default.temporaryDirectory.appendingPathComponent("wd-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return URL(fileURLWithPath: ExecutablePolicy.realPath(d.path)!)
    }

    /// A copy of /bin/sleep that runs from a temporary folder: an Apple binary copied out of
    /// the system is killed by macOS at start, so it is re-signed ad-hoc (= "unsigned" for
    /// the guard's rules, like a miner dropped into /tmp).
    private func unsignedSleep(in d: URL) throws -> String {
        let copy = d.appendingPathComponent("sleep").path
        try FileManager.default.copyItem(atPath: "/bin/sleep", toPath: copy)
        try codesign(["--force", "-s", "-", copy])
        return copy
    }

    private func codesign(_ args: [String]) throws {
        let cs = Process()
        cs.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
        cs.arguments = args
        cs.standardError = FileHandle.nullDevice
        try cs.run(); cs.waitUntilExit()
        #expect(cs.terminationStatus == 0)
    }

    private struct NoSignatures: CodeSignatureChecking {
        func trust(ofFile path: String) -> CodeTrust { .none }
    }

    @Test func test_watchdog_stops_a_child_the_rules_refuse() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let copy = try unsignedSleep(in: d)
        let policy = ExecutablePolicy(trust: ToolTrust(file: d.appendingPathComponent("t.json")),
                                      signatures: NoSignatures(), untrustedRoots: [d.path])
        let g = ProcessGuard(policy: policy, log: GuardLog(directory: d.appendingPathComponent("log")))
        g.mode = .enforce
        var stopped: [String] = []
        g.onEvent = { if case .stopped(_, let exe, _) = $0 { stopped.append(exe) } }
        let wd = GuardWatchdog(guard: g)

        let ok = try spawn("/bin/sleep", ["30"])
        let bad = try spawn(copy, ["30"])
        defer { ok.terminate(); bad.terminate() }
        Thread.sleep(forTimeInterval: 0.3)
        wd.tick()
        bad.waitUntilExit()
        #expect(bad.terminationReason == .uncaughtSignal)
        #expect(bad.terminationStatus == SIGTERM)   // the guard's SIGTERM, not macOS
        #expect(ok.isRunning)
        #expect(stopped == [copy])
    }

    @Test func test_watchdog_report_only_does_not_stop() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let copy = try unsignedSleep(in: d)
        let policy = ExecutablePolicy(trust: ToolTrust(file: d.appendingPathComponent("t.json")),
                                      signatures: NoSignatures(), untrustedRoots: [d.path])
        let g = ProcessGuard(policy: policy, log: GuardLog(directory: d.appendingPathComponent("log")))
        g.mode = .reportOnly
        let bad = try spawn(copy, ["30"])
        defer { bad.terminate() }
        Thread.sleep(forTimeInterval: 0.3)
        GuardWatchdog(guard: g).tick()
        Thread.sleep(forTimeInterval: 0.3)
        #expect(bad.isRunning)
        #expect(try String(contentsOf: g.log.file, encoding: .utf8).contains("would stop"))
    }

    @Test func test_watchdog_stops_a_cpu_hog() throws {
        let d = try tempDir()
        defer { try? FileManager.default.removeItem(at: d) }
        let g = ProcessGuard(policy: ExecutablePolicy(trust: ToolTrust(file: d.appendingPathComponent("t.json"))),
                             log: GuardLog(directory: d.appendingPathComponent("log")))
        g.mode = .enforce
        var reasons: [GuardEvent.StopReason] = []
        g.onEvent = { if case .stopped(_, _, let r) = $0 { reasons.append(r) } }
        // tiny limits instead of 30 % / 90 s
        let wd = GuardWatchdog(guard: g, threshold: 0.001, window: 0.5)
        let hog = try spawn("/usr/bin/yes", [])
        defer { hog.terminate() }
        Thread.sleep(forTimeInterval: 0.2)
        wd.tick()
        Thread.sleep(forTimeInterval: 0.4)
        wd.tick()
        Thread.sleep(forTimeInterval: 0.4)
        wd.tick()
        hog.waitUntilExit()
        #expect(reasons == [.cpu])
    }

    // MARK: self check on a throw-away bundle

    private func makeBundle(receipt: SelfCheck.Receipt?) throws -> (URL, URL) {
        let d = try tempDir()
        let app = d.appendingPathComponent("Test.app")
        let macos = app.appendingPathComponent("Contents/MacOS")
        let res = app.appendingPathComponent("Contents/Resources")
        try FileManager.default.createDirectory(at: macos, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: res, withIntermediateDirectories: true)
        let exe = macos.appendingPathComponent("Test")
        try FileManager.default.copyItem(atPath: "/bin/echo", toPath: exe.path)
        let plist: [String: Any] = ["CFBundleExecutable": "Test", "CFBundleIdentifier": "test.fling.guard",
                                    "CFBundlePackageType": "APPL"]
        try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            .write(to: app.appendingPathComponent("Contents/Info.plist"))
        if let receipt {
            try PropertyListEncoder().encode(receipt).write(to: res.appendingPathComponent(SelfCheck.receiptName))
        }
        try codesign(["--force", "--deep", "--options", "runtime", "-s", "-", app.path])
        return (app, exe)
    }

    private func record(_ sha: String, in d: URL) throws -> URL {
        let url = d.appendingPathComponent("install.plist")
        try PropertyListEncoder().encode(SelfCheck.InstallRecord(binarySHA256: sha, app: nil)).write(to: url)
        return url
    }

    @Test func test_self_check_source_build_is_report_only() throws {
        let (app, exe) = try makeBundle(receipt: nil)
        let d = app.deletingLastPathComponent()
        defer { try? FileManager.default.removeItem(at: d) }
        let none = d.appendingPathComponent("no-record.plist")
        let r = SelfCheck.run(bundle: app, executable: exe, record: none, enforceOverride: false)
        #expect(r.problems == [])
        #expect(r.signer == "ad-hoc")
        #expect(r.hardenedRuntime)
        #expect(r.mode == .reportOnly)
        #expect(SelfCheck.run(bundle: app, executable: exe, record: none, enforceOverride: true).mode == .enforce)
    }

    private let verified = SelfCheck.Receipt(verified: true, version: "9.9.9", releaseSHA256: nil, installedAt: nil)

    @Test func test_self_check_verified_install_and_tampering() throws {
        let (app, exe) = try makeBundle(receipt: verified)
        let d = app.deletingLastPathComponent()
        defer { try? FileManager.default.removeItem(at: d) }
        let rec = try record(try #require(FileHash.sha256(exe)), in: d)
        let ok = SelfCheck.run(bundle: app, executable: exe, record: rec, enforceOverride: false)
        #expect(ok.problems == [])
        #expect(ok.mode == .enforce)
        #expect(ok.receipt?.version == "9.9.9")

        // a file added to the bundle after signing
        try Data("evil".utf8).write(to: app.appendingPathComponent("Contents/Resources/payload.dylib"))
        let bad = SelfCheck.run(bundle: app, executable: exe, record: rec, enforceOverride: false)
        #expect(bad.problems.contains(SelfCheck.Problem.signatureBroken), "\(bad)")
        #expect(bad.mode == .enforce)
    }

    @Test func test_self_check_program_not_the_installed_one() throws {
        let (app, exe) = try makeBundle(receipt: verified)
        let d = app.deletingLastPathComponent()
        defer { try? FileManager.default.removeItem(at: d) }
        // e.g. the bundle re-signed after its code was changed: the signature is fine again,
        // the recorded hash is not
        let r = SelfCheck.run(bundle: app, executable: exe, record: try record(String(repeating: "0", count: 64), in: d),
                              enforceOverride: false)
        #expect(r.problems == [SelfCheck.Problem.binaryChanged])
        let missing = SelfCheck.run(bundle: app, executable: exe, record: d.appendingPathComponent("gone.plist"),
                                    enforceOverride: false)
        #expect(missing.problems == [SelfCheck.Problem.recordMissing])
        #expect(missing.mode == .enforce)
    }
}
}
