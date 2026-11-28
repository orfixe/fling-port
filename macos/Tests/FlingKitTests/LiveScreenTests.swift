// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import XCTest
@testable import FlingKit

final class LiveScreenTests: XCTestCase {

    func testScreenArgumentsLowLatencyAndDefaults() {
        let args = Scrcpy.arguments(serial: "10.0.0.5:5555", title: "T", mode: .screen,
                                    options: LiveOptions(), lowLatency: true)
        XCTAssertTrue(Array(args.prefix(3)) == ["-s", "10.0.0.5:5555", "--window-title=T"])
        XCTAssertTrue(args.contains("--mouse=sdk"))          // the Mac's pointer is the default
        XCTAssertTrue(args.contains("--no-audio"))
        XCTAssertTrue(args.contains("--max-size=1280"))
        XCTAssertTrue(args.contains("--max-fps=60"))
        XCTAssertTrue(args.contains("--video-buffer=0"))
        XCTAssertTrue(args.contains("--video-codec-options=priority:int=0,latency:int=1"))
        XCTAssertTrue(!(args.contains("--keyboard=disabled")))
    }

    func testPlainStartAndOptions() {
        let o = LiveOptions(quality: .fast, mouse: .tvCursor, keyboardToTV: false, sound: true)
        let args = Scrcpy.arguments(serial: "s", title: "T", mode: .screen, options: o, lowLatency: false)
        XCTAssertTrue(!(args.contains { $0.hasPrefix("--video-codec-options") }))
        XCTAssertTrue(args.contains("--mouse=uhid"))
        XCTAssertTrue(args.contains("--keyboard=disabled"))
        XCTAssertTrue(!(args.contains("--no-audio")))
        XCTAssertTrue(args.contains("--max-size=960"))
        XCTAssertTrue(args.contains("--max-fps=60"))
        // the UHID → taps fallback overrides the mouse
        let fallback = Scrcpy.arguments(serial: "s", title: "T", mode: .screen, options: o,
                                        lowLatency: false, mouse: "sdk")
        XCTAssertTrue(fallback.contains("--mouse=sdk"))
    }

    func testWatchAndMouseOnly() {
        let watch = Scrcpy.arguments(serial: "s", title: "T", mode: .screen,
                                     options: LiveOptions(mouse: .watch), lowLatency: true)
        XCTAssertTrue(watch.contains("--no-control"))
        XCTAssertTrue(!(watch.contains { $0.hasPrefix("--mouse=") }))
        let mouse = Scrcpy.arguments(serial: "s", title: "T", mode: .mouseOnly,
                                     options: LiveOptions(), lowLatency: true)
        XCTAssertTrue(mouse.contains("--no-video"))
        XCTAssertTrue(mouse.contains("--mouse=uhid"))
        XCTAssertTrue(!(mouse.contains { $0.hasPrefix("--video-codec-options") }))
    }

    func testUninjectableText() {
        let out = "WARN: Could not inject char u+0444\nINFO: x\nWARN: Could not inject char u+00e9\n"
        XCTAssertTrue(Scrcpy.uninjectableText(in: out) == "фé")
        XCTAssertTrue(Scrcpy.uninjectableText(in: "nothing here") == "")
    }

    func testExitClassification() {
        let mc = "ERROR: Encoding error: android.media.MediaCodec$CodecException: Error 0xffffffea"
        XCTAssertTrue(Scrcpy.encoderRefused(output: mc, quick: true, usedLowLatency: true))
        XCTAssertTrue(!(Scrcpy.encoderRefused(output: mc, quick: false, usedLowLatency: true)))
        XCTAssertTrue(!(Scrcpy.encoderRefused(output: mc, quick: true, usedLowLatency: false)))
        XCTAssertTrue(Scrcpy.uhidMissing(output: "ERROR: Could not open /dev/uhid", quick: true, mouse: "uhid"))
        XCTAssertTrue(!(Scrcpy.uhidMissing(output: "ERROR: Could not open /dev/uhid", quick: true, mouse: "sdk")))
        XCTAssertTrue(Scrcpy.lastError(in: "INFO: a\nERROR: first\nERROR: Server connection failed\n")
                == "Server connection failed")
        XCTAssertTrue(Scrcpy.lastError(in: "INFO: ok") == nil)
    }

    func testToolLookup() {
        let found = LiveTools.find("scrcpy", environment: ["PATH": "/usr/bin:/bin"], home: "/Users/x",
                                   isExecutable: { $0 == "/usr/local/bin/scrcpy" })
        XCTAssertTrue(found == "/usr/local/bin/scrcpy")
        let adb = LiveTools.find("adb", environment: [:], home: "/Users/x",
                                 isExecutable: { $0 == "/Users/x/Library/Android/sdk/platform-tools/adb" })
        XCTAssertTrue(adb == "/Users/x/Library/Android/sdk/platform-tools/adb")
        let override = LiveTools.find("adb", environment: ["ADB": "/custom/adb"], home: "/Users/x",
                                      isExecutable: { _ in true })
        XCTAssertTrue(override == "/custom/adb")
        XCTAssertTrue(LiveTools.find("scrcpy", environment: [:], home: "/h", isExecutable: { _ in false }) == nil)
    }

    func testADBParsing() {
        let devices = "List of devices attached\n10.0.0.5:5555\tunauthorized\n10.0.0.5:37001\tdevice\n10.0.0.50:5555\tdevice\n"
        let parsed = LiveADB.parseDevices(devices)
        XCTAssertTrue(parsed.count == 3)
        XCTAssertTrue(LiveADB.match(parsed, ip: "10.0.0.5")?.serial == "10.0.0.5:37001")
        XCTAssertTrue(LiveADB.match(parsed, ip: "10.0.0.50")?.serial == "10.0.0.50:5555")
        XCTAssertTrue(LiveADB.match(parsed, ip: "10.0.0.6") == nil)
        let mdns = "List of discovered mdns services\nadb-XYZ\t_adb-tls-connect._tcp\t10.0.0.5:41234\n"
        XCTAssertTrue(LiveADB.parseMDNS(mdns)["10.0.0.5"] == 41234)
    }

    func testSerialConnectsAnnouncedPort() throws {
        final class FakeADB: ProcessRunning, @unchecked Sendable {
            var connected = false
            var calls: [[String]] = []
            func run(_ executable: String, _ args: [String], timeout: TimeInterval) throws -> ProcessResult {
                calls.append(args)
                switch args.first {
                case "devices":
                    return ProcessResult(exitCode: 0, stdout: "List of devices attached\n"
                                         + (connected ? "10.0.0.5:41234\tdevice\n" : ""), stderr: "")
                case "mdns":
                    return ProcessResult(exitCode: 0, stdout: "x\t_adb-tls-connect._tcp\t10.0.0.5:41234\n", stderr: "")
                case "connect":
                    if args[1] == "10.0.0.5:41234" { connected = true }
                    return ProcessResult(exitCode: 0, stdout: "connected", stderr: "")
                default:
                    return ProcessResult(exitCode: 1, stdout: "", stderr: "")
                }
            }
        }
        let fake = FakeADB()
        XCTAssertTrue(try LiveADB(adb: "/adb", runner: fake).serial(for: "10.0.0.5") == "10.0.0.5:41234")
        XCTAssertTrue(!(fake.calls.contains(["connect", "10.0.0.5:5555"])))

        let unauthorized = FakeADBUnauthorized()
        XCTAssertThrowsError(try LiveADB(adb: "/adb", runner: unauthorized).serial(for: "10.0.0.5")) {
            XCTAssertEqual($0 as? LiveError, .unauthorized)
        }
    }

    @MainActor
    func testOptionsAreRemembered() {
        let suite = "live-tests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: suite)!
        defer { d.removePersistentDomain(forName: suite) }
        let a = TVScreen(defaults: d)
        XCTAssertTrue(a.options == LiveOptions())
        a.setOptions(LiveOptions(quality: .fast, mouse: .tvCursor, keyboardToTV: false, sound: true), ip: nil)
        let b = TVScreen(defaults: d)
        XCTAssertTrue(b.options.quality == .fast)
        XCTAssertTrue(b.options.mouse == .tvCursor)
        XCTAssertTrue(!(b.options.keyboardToTV))
        XCTAssertTrue(b.options.sound)
        XCTAssertTrue(!(b.prefersPlain("10.0.0.5")))
    }
}

private final class FakeADBUnauthorized: ProcessRunning, @unchecked Sendable {
    func run(_ executable: String, _ args: [String], timeout: TimeInterval) throws -> ProcessResult {
        ProcessResult(exitCode: 0, stdout: args.first == "devices"
                      ? "List of devices attached\n10.0.0.5:5555\tunauthorized\n" : "", stderr: "")
    }
}
