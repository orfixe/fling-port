// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
import Foundation
import Testing
@testable import FlingKit

/// Port (macOS guard): which programs may run, trust-on-first-use, the launch gate.
extension GuardSuite {
@Suite final class GuardPolicyTests {
    private struct FakeSignatures: CodeSignatureChecking {
        var trusted: [String: CodeTrust] = [:]
        func trust(ofFile path: String) -> CodeTrust { trusted[path] ?? .none }
    }

    private var dir: URL!        // a user-owned folder that is not "temporary" for the policy
    private var tempZone: URL!   // stands for /tmp, ~/Downloads… in these tests
    private var trust: ToolTrust!

    init() throws {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("guard-\(UUID().uuidString)")
        dir = base.appendingPathComponent("tools")
        tempZone = base.appendingPathComponent("tmpzone")
        for d in [dir!, tempZone!] { try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true) }
        dir = URL(fileURLWithPath: ExecutablePolicy.realPath(dir.path)!)
        tempZone = URL(fileURLWithPath: ExecutablePolicy.realPath(tempZone.path)!)
        trust = ToolTrust(file: base.appendingPathComponent("trust.json"))
    }

    deinit {
        try? FileManager.default.removeItem(at: dir.deletingLastPathComponent())
    }

    private func policy(_ sigs: FakeSignatures = FakeSignatures()) -> ExecutablePolicy {
        ExecutablePolicy(trust: trust, signatures: sigs, untrustedRoots: [tempZone.path])
    }

    @discardableResult
    private func write(_ name: String, _ text: String, in d: URL? = nil, mode: Int = 0o755) throws -> String {
        let url = (d ?? dir).appendingPathComponent(name)
        try Data(text.utf8).write(to: url)
        try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: url.path)
        return url.path
    }

    private func copyBinary(_ from: String, to name: String, in d: URL? = nil) throws -> String {
        let to = (d ?? dir).appendingPathComponent(name).path
        try FileManager.default.copyItem(atPath: from, toPath: to)
        return to
    }

    // MARK: rules

    @Test func test_system_programs_are_allowed() {
        let p = ExecutablePolicy(trust: trust)   // real signature checks
        #expect(p.evaluate("/bin/sh").isAllowed)
        #expect(p.evaluate("/usr/bin/osascript").isAllowed)
        #expect(ExecutablePolicy.isAdminOnly("/usr/bin/env"))
        #expect(!(ExecutablePolicy.isAdminOnly(dir.path)))
        #expect(!(ExecutablePolicy.isAdminOnly("/private/tmp")))   // world-writable
    }

    @Test func test_apple_signed_copy_outside_system_folders_is_allowed_by_signature() throws {
        let copy = try copyBinary("/bin/echo", to: "echo")
        #expect(!(ExecutablePolicy.isAdminOnly(copy)))
        #expect(ExecutablePolicy(trust: trust).evaluate(copy).isAllowed)
    }

    @Test func test_refusals() throws {
        let p = policy()
        #expect(p.evaluate("echo") == .refused(ExecutablePolicy.Reason.notAbsolute))
        #expect(p.evaluate(dir.appendingPathComponent("nope").path) == .refused(ExecutablePolicy.Reason.missing))
        let plain = try write("data.txt", "hello", mode: 0o644)
        #expect(p.evaluate(plain) == .refused(ExecutablePolicy.Reason.notExecutable))
        let junk = try write("junk", "hello")
        #expect(p.evaluate(junk) == .refused(ExecutablePolicy.Reason.unknownFormat))
        let noInterp = try write("bad.sh", "#!relative/sh\necho\n")
        #expect(p.evaluate(noInterp) == .refused(ExecutablePolicy.Reason.badShebang))
        let inner = try write("inner.sh", "#!/bin/sh\necho\n")
        let nested = try write("nested.sh", "#!\(inner)\necho\n")
        #expect(p.evaluate(nested) == .refused(ExecutablePolicy.Reason.nestedScript))
    }

    @Test func test_unsigned_program_is_asked_then_pinned_then_reasked_after_change() throws {
        let tool = try copyBinary("/bin/echo", to: "tool")
        let p = policy()   // fake checker: the copy counts as unsigned
        guard case .needsTrust(let r) = p.evaluate(tool) else { Issue.record("should ask"); return }
        #expect(r.items.count == 1)
        #expect(r.items[0].kind == .program)
        #expect(!(r.isChange))
        #expect(r.items[0].sha256 == FileHash.sha256(URL(fileURLWithPath: tool)))
        trust.add(r.items, source: "test")
        #expect(p.evaluate(tool).isAllowed)
        // the list survives a restart
        #expect(ToolTrust(file: trust.file).pin(for: tool)?.sha256 == r.items[0].sha256)

        // "brew upgrade": same path, other bytes
        try FileManager.default.removeItem(atPath: tool)
        _ = try copyBinary("/bin/ls", to: "tool")
        guard case .needsTrust(let r2) = p.evaluate(tool) else { Issue.record("should re-ask"); return }
        #expect(r2.isChange)
        #expect(r2.items[0].previousSHA256 == r.items[0].sha256)
    }

    @Test func test_developer_id_is_allowed() throws {
        let tool = try copyBinary("/bin/echo", to: "signed")
        let p = policy(FakeSignatures(trusted: [tool: .developerID(team: "ABCDE12345")]))
        #expect(p.evaluate(tool).isAllowed)
    }

    @Test func test_symlink_into_a_temporary_folder_is_judged_by_its_target() throws {
        let miner = try copyBinary("/bin/echo", to: "miner", in: tempZone)
        let link = dir.appendingPathComponent("catt").path
        try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: miner)
        #expect(policy().evaluate(link) == .refused(ExecutablePolicy.Reason.inTemp))
        // even a pin does not make unsigned code in a temporary folder acceptable
        trust.add([.init(path: miner, sha256: FileHash.sha256(URL(fileURLWithPath: miner))!, kind: .program, previousSHA256: nil)], source: "test")
        #expect(policy().evaluate(link) == .refused(ExecutablePolicy.Reason.inTemp))
        // a script there too
        let s = try write("x.sh", "#!/bin/sh\n", in: tempZone)
        #expect(policy().evaluate(s) == .refused(ExecutablePolicy.Reason.inTemp))
    }

    @Test func test_script_like_catt_pins_script_and_interpreter_together() throws {
        // uv layout: ~/.local/bin/catt → tools/catt/bin/catt, "#!…/bin/python" → unsigned python
        let python = try copyBinary("/bin/echo", to: "python3.12")
        let venvPython = dir.appendingPathComponent("python").path
        try FileManager.default.createSymbolicLink(atPath: venvPython, withDestinationPath: python)
        let script = try write("catt-entry", "#!\(venvPython)\nimport sys\n")
        let link = dir.appendingPathComponent("catt").path
        try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: script)

        let p = policy()
        guard case .needsTrust(let r) = p.evaluate(link) else { Issue.record("should ask"); return }
        #expect(Set(r.items.map(\.kind)) == [.interpreter, .script])
        #expect(Set(r.items.map(\.path)) == [python, script])
        #expect(r.displayName == "catt")
        trust.add(r.items, source: "test")
        #expect(p.evaluate(link).isAllowed)

        // the script changes (uv tool upgrade) → only the script is asked about again
        try write("catt-entry", "#!\(venvPython)\nimport os\n")
        guard case .needsTrust(let r2) = p.evaluate(link) else { Issue.record("should re-ask"); return }
        #expect(r2.items.map(\.kind) == [.script])
        #expect(r2.isChange)
    }

    @Test func test_script_with_system_interpreter_and_env() throws {
        let p = policy()
        let s1 = try write("a.sh", "#!/bin/sh -e\necho\n")
        guard case .needsTrust(let r1) = p.evaluate(s1) else { Issue.record(""); return }
        #expect(r1.items.map(\.kind) == [.script])   // /bin/sh needs no pin
        let s2 = try write("b.sh", "#!/usr/bin/env -S sh -e\necho\n")
        #expect(p.interpreterPath(Array("#!/usr/bin/env -S sh -e\n".utf8)) == "/bin/sh")
        guard case .needsTrust(let r2) = p.evaluate(s2) else { Issue.record(""); return }
        #expect(r2.items.map(\.kind) == [.script])
        #expect(p.interpreterPath(Array("#!/usr/bin/env FOO=1 /bin/bash\n".utf8)) == "/bin/bash")
        #expect(p.interpreterPath(Array("#!/usr/bin/env ../evil\n".utf8)) == nil)
    }

    // MARK: the gate

    @Test func test_enforce_asks_once_and_remembers_the_answer() throws {
        let tool = try copyBinary("/bin/echo", to: "tool")
        let g = ProcessGuard(policy: policy(), log: GuardLog(directory: dir.appendingPathComponent("logs")))
        g.mode = .enforce
        var asked = 0
        var events: [GuardEvent] = []
        g.onEvent = { events.append($0) }

        g.askToTrust = { _ in asked += 1; return false }
        #expect(throws: GuardRefusal(executable: tool, reason: ProcessGuard.declinedReason)) { try g.authorize(tool) }
        #expect(throws: (any Error).self) { try g.authorize(tool) }   // not asked again this run
        #expect(asked == 1)

        let other = try copyBinary("/bin/ls", to: "other")
        g.askToTrust = { _ in asked += 1; return true }
        #expect(throws: Never.self) { try g.authorize(other) }
        #expect(throws: Never.self) { try g.authorize(other) }
        #expect(asked == 2)
        #expect(trust.pin(for: other) != nil)

        g.askToTrust = nil
        let third = try copyBinary("/bin/cat", to: "third")
        #expect(throws: (any Error).self) { try g.authorize(third) }   // nobody to ask: refused

        #expect(throws: (any Error).self) { try g.authorize(dir.appendingPathComponent("missing").path) }
        #expect(events.contains {
            if case .refused(_, let r) = $0 { return r == ExecutablePolicy.Reason.missing } else { return false }
        })
        #expect(throws: Never.self) { try g.authorize("/bin/sh") }
    }

    @Test func test_report_only_never_blocks() throws {
        let tool = try copyBinary("/bin/echo", to: "tool")
        let g = ProcessGuard(policy: policy(), log: GuardLog(directory: dir.appendingPathComponent("logs")))
        g.mode = .reportOnly
        g.askToTrust = { _ in Issue.record("no questions in report-only mode"); return false }
        #expect(throws: Never.self) { try g.authorize(tool) }
        #expect(throws: Never.self) { try g.authorize(dir.appendingPathComponent("missing").path) }
        let log = try String(contentsOf: g.log.file, encoding: .utf8)
        #expect(log.contains("report-only: would ask to trust"))
        #expect(log.contains("report-only: would refuse"))
    }

    @Test func test_register_for_the_tools_window() throws {
        let tool = try copyBinary("/bin/echo", to: "adb")
        let g = ProcessGuard(policy: policy(), log: GuardLog(directory: dir.appendingPathComponent("logs")))
        #expect(throws: (any Error).self) { try ToolTrust.register(path: tool, expectedSHA256: String(repeating: "0", count: 64), guard: g) }
        #expect(trust.pin(for: tool) == nil)
        let sha = FileHash.sha256(URL(fileURLWithPath: tool))!
        let pins = try ToolTrust.register(path: tool, expectedSHA256: sha.uppercased(), guard: g)
        #expect(pins.map(\.path) == [tool])
        #expect(pins.first?.source == "tools")
        g.mode = .enforce
        g.askToTrust = { _ in Issue.record("registered: no question"); return false }
        #expect(throws: Never.self) { try g.authorize(tool) }
        #expect(try ToolTrust.register(path: "/bin/sh", guard: g) == [])   // needs no pin
        #expect(throws: (any Error).self) { try ToolTrust.register(path: dir.appendingPathComponent("none").path, guard: g) }
        ToolTrust.unregister(path: tool, guard: g)
        #expect(trust.pin(for: tool) == nil)
    }

    @Test func test_system_runner_goes_through_the_shared_guard() throws {
        let script = try write("hello.sh", "#!/bin/sh\necho hi\n", in: tempZone)
        let g = ProcessGuard.shared
        let saved = g.mode
        defer { g.mode = saved }
        g.mode = .enforce
        #expect(throws: GuardRefusal.self) { try SystemProcessRunner().run(script, [], timeout: 5) }
        #expect(try GuardedProcess.run("/bin/echo", ["ok"]).stdout == "ok\n")
        g.mode = .reportOnly
        // report-only: logged, still runs (the shared policy's temp roots include this folder)
        #expect(try SystemProcessRunner().run(script, [], timeout: 5).stdout == "hi\n")
    }

    // MARK: log

    @Test func test_log_rotates() throws {
        let log = GuardLog(directory: dir.appendingPathComponent("rot"), maxBytes: 200, keep: 2)
        for i in 0..<40 { log.info("line \(i) " + String(repeating: "x", count: 20)) }
        let fm = FileManager.default
        #expect(fm.fileExists(atPath: log.file.path))
        #expect(fm.fileExists(atPath: log.directory.appendingPathComponent("guard.log.1").path))
        #expect(fm.fileExists(atPath: log.directory.appendingPathComponent("guard.log.2").path))
        #expect(!(fm.fileExists(atPath: log.directory.appendingPathComponent("guard.log.3").path)))
        let size = try #require(fm.attributesOfItem(atPath: log.file.path)[.size] as? Int)
        #expect(size <= 200)
        #expect(try String(contentsOf: log.file, encoding: .utf8).contains("line 39"))
        log.once("k", "info", "only once"); log.once("k", "info", "only once")
        #expect(try String(contentsOf: log.file, encoding: .utf8).components(separatedBy: "only once").count == 2)
    }
}
}
