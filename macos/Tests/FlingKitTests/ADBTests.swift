// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Testing
@testable import FlingKit

/// Answers adb commands from a script; never runs adb, never touches the network.
final class ADBScriptRunner: ProcessRunning, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var calls: [[String]] = []
    /// First matching rule wins: a sub-sequence of args → result.
    var rules: [(match: [String], result: ProcessResult)] = []

    func on(_ match: [String], stdout: String = "", stderr: String = "", exit: Int32 = 0) {
        rules.append((match, ProcessResult(exitCode: exit, stdout: stdout, stderr: stderr)))
    }

    func run(_ executable: String, _ args: [String], timeout: TimeInterval) throws -> ProcessResult {
        lock.lock(); calls.append(args); lock.unlock()
        for r in rules where args.joined(separator: " ").contains(r.match.joined(separator: " ")) {
            return r.result
        }
        return ProcessResult(exitCode: 0, stdout: "", stderr: "")
    }
}

// MARK: - captured sample outputs

private let mdnsSample = """
List of discovered mdns services
adb-1A2B3C4D-xyz12\t_adb-tls-connect._tcp\t192.168.1.50:37123
adb-1A2B3C4D-xyz12\t_adb-tls-pairing._tcp.\t192.168.1.50:41233
SHIELD\t_adb._tcp\t192.168.1.60:5555
weird\t_other._tcp\t192.168.1.70:1234
fe80\t_adb-tls-connect._tcp\t[fe80::1]:5555
"""

private let devicesSample = """
* daemon not running; starting now at tcp:15037
* daemon started successfully
List of devices attached
192.168.1.50:37123\tdevice
192.168.1.60:5555\tunauthorized
emulator-5554\toffline

"""

private let dumpsysSample = """
MEDIA SESSION SERVICE (dumpsys media_session)

Media button session is com.google.android.youtube.tv/YouTube (userId=0)
  Sessions Stack - have 3 sessions:
    MediaShell com.google.android.apps.mediashell/CastSession (userId=0)
      ownerPid=811, ownerUid=10040, userId=0
      package=com.google.android.apps.mediashell
      active=true
      state=PlaybackState {state=3, position=5000, buffered position=0, speed=1.0, updated=100000, actions=0, custom actions=[], active item id=-1, error=null}
      metadata: size=3, description=Old cast, null, null
    YouTube com.google.android.youtube.tv/YouTube (userId=0)
      ownerPid=1234, ownerUid=10085, userId=0
      package=com.google.android.youtube.tv
      active=true
      flags=3
      state=PlaybackState {state=3, position=60000, buffered position=90000, speed=1.0, updated=200000, actions=3669967, custom actions=[], active item id=0, error=null}
      metadata: size=7, description=Never Gonna Give You Up, Hello, World, Rick Astley, null
    Spotify com.spotify.tv.android/spotify (userId=0)
      package=com.spotify.tv.android
      active=false
      state=PlaybackState {state=2, position=12000, buffered position=0, speed=0.0, updated=150000, actions=0, custom actions=[], active item id=0, error=null}
      metadata: size=5, description=Song, Band, Album
__UP__
210.50 400.00
"""

@Suite struct ADBParseTests {

    @Test func test_mdns_services_by_ip() {
        let m = ADBParse.mdnsServices(mdnsSample)
        #expect(m["192.168.1.50"] == ADBAnnounce(connect: 37123, pairing: 41233))
        #expect(m["192.168.1.60"] == ADBAnnounce(adb: 5555))
        #expect(m["192.168.1.70"] == nil)
        #expect(m.count == 2)
    }

    @Test func test_devices_states() {
        let d = ADBParse.devices(devicesSample)
        #expect(d["192.168.1.50:37123"] == "device")
        #expect(d["192.168.1.60:5555"] == "unauthorized")
        #expect(d["emulator-5554"] == "offline")
        #expect(d["*"] == nil)
        #expect(ADBParse.deviceState(devicesSample, ip: "192.168.1.50")?.port == 37123)
        #expect(ADBParse.deviceState(devicesSample, ip: "192.168.1.5") == nil)
    }

    @Test func test_now_playing_prefers_live_app_over_stale_mediashell() throws {
        let at = Date(timeIntervalSince1970: 1000)
        let np = try #require(ADBParse.nowPlaying(dumpsysSample, at: at))
        #expect(np.state == .playing)
        #expect(np.package == "com.google.android.youtube.tv")
        #expect(np.title == "Never Gonna Give You Up, Hello, World")    // commas in the title survive
        #expect(np.artist == "Rick Astley")
        // 60 s at update + (210.5 s uptime − 200 s updated) × 1.0
        #expect(abs(try #require(np.elapsed) - 70.5) < 0.01)
        #expect(abs(try #require(np.position(now: at.addingTimeInterval(2))) - 72.5) < 0.01)
    }

    @Test func test_now_playing_paused_and_idle() throws {
        let paused = dumpsysSample.replacingOccurrences(of: "state=3", with: "state=1")
        let np = try #require(ADBParse.nowPlaying(paused))
        #expect(np.state == .paused)
        #expect(np.title == "Song")
        #expect(np.artist == "Band")
        #expect(abs(try #require(np.elapsed) - 12) < 0.01)

        let idle = "  Sessions Stack - have 0 sessions:\n__UP__\n5.0 1.0\n"
        #expect(ADBParse.nowPlaying(idle)?.state == .idle)
        #expect(ADBParse.nowPlaying("error: device offline") == nil)
        let nullTitle = "package=a.b\nstate=PlaybackState {state=3, position=0, speed=1.0, updated=0}\nmetadata: size=0, description=null\n__UP__\n1 1"
        #expect(ADBParse.nowPlaying(nullTitle)?.state == .idle)
    }

    /// Android 14 (a Mi TV, casting through mediashell) prints the state by name:
    /// "state=PLAYING(3)". Before the fix the parser only knew "state=3" and saw nothing playing.
    @Test func test_now_playing_android14_named_state() throws {
        let out = """
          Sessions Stack - have 2 sessions:
            mediashell com.google.android.apps.mediashell/mediashell (userId=0)
              package=com.google.android.apps.mediashell
              active=true
              state=PlaybackState {state=PLAYING(3), position=968, buffered position=0, speed=1.0, updated=93303314, actions=775, custom actions=[], active item id=-1, error=null}
              metadata: size=11, description=Big_Buck_Bunny_720_10s_30MB, null, null
            A2dpMediaPlayer com.android.bluetooth/A2dpMediaPlayer (userId=0)
              package=com.android.bluetooth
              active=false
              state=PlaybackState {state=ERROR(7), position=0, buffered position=0, speed=0.0, updated=27206, actions=0, custom actions=[], active item id=-1, error=Звук через Bluetooth отключен}
              metadata: null
        __UP__
        93303.490 1000.00
        """
        let np = try #require(ADBParse.nowPlaying(out))
        #expect(np.state == .playing)
        #expect(np.title == "Big_Buck_Bunny_720_10s_30MB")
        #expect(np.artist == nil)
        #expect(np.package == "com.google.android.apps.mediashell")
        // 0.968 s at update + (93303.490 s − 93303.314 s) × 1.0
        #expect(abs(try #require(np.elapsed) - 1.144) < 0.01)

        let paused = out.replacingOccurrences(of: "state=PLAYING(3)", with: "state=PAUSED(2)")
        #expect(ADBParse.nowPlaying(paused)?.state == .paused)
    }

    @Test func test_install_failures_in_plain_words() {
        #expect(ADBParse.installFailure(exitCode: 0, output: "Performing Streamed Install\nSuccess") == nil)
        #expect(ADBParse.installFailure(exitCode: 1, output:
            "adb: failed to install x.apk: Failure [INSTALL_FAILED_VERSION_DOWNGRADE: Downgrade detected]") == "The TV already has a newer version.")
        #expect(ADBParse.installFailure(exitCode: 1, output: "Failure [INSTALL_FAILED_WEIRD]") == "INSTALL_FAILED_WEIRD")
        #expect(ADBParse.installFailure(exitCode: 1, output: "error: closed\n") == "error: closed")
    }

    @Test func test_youtube_search_artwork_and_duration() throws {
        let html = #"xx "videoRenderer":{"videoId":"dQw4w9WgXcQ","thumbnail":{}, "lengthText":{"accessibility":{"accessibilityData":{"label":"3 minutes, 33 seconds"}},"simpleText":"3:33"} yy"#
        let hit = try #require(ADBParse.youtubeSearch(html))
        #expect(hit.image.absoluteString == "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg")
        #expect(hit.duration == 213)
        #expect(ADBParse.youtubeSearch("<html></html>") == nil)
    }

    @Test func test_locator_order_and_path() {
        let c = ADBLocator.candidates(home: "/Users/me", env: ["PATH": "/usr/bin:/opt/homebrew/bin"])
        #expect(c.first == "/opt/homebrew/bin/adb")
        #expect(c.contains("/Users/me/Library/Android/sdk/platform-tools/adb"))
        #expect(c.contains("/usr/bin/adb"))
        #expect(c.filter { $0 == "/opt/homebrew/bin/adb" }.count == 1)
        #expect(ADBLocator.find(isExecutable: { $0 == "/usr/local/bin/adb" },
                                       home: "/Users/me", env: [:]) == "/usr/local/bin/adb")
        #expect(ADBLocator.find(isExecutable: { _ in false }, home: "/x", env: [:]) == nil)
    }
}

@Suite struct ADBClientTests {

    @Test func test_every_call_uses_flings_own_server_port() throws {
        let r = ADBScriptRunner()
        let c = ADBClient(executable: "/opt/homebrew/bin/adb", runner: r, store: MemoryADBPortStore(),
                          probe: { _, _ in false })
        #expect(!c.used)
        c.stopOwnServer()                         // never used: nothing to stop
        #expect(r.calls.isEmpty)
        _ = c.mdns()
        c.stopOwnServer()
        #expect(r.calls.first == ["-P", "15037", "mdns", "services"])
        #expect(r.calls.last == ["-P", "15037", "kill-server"])
    }

    @Test func test_reach_tries_remembered_then_announced_port_and_remembers_the_one_that_works() {
        let r = ADBScriptRunner()
        r.on(["devices"], stdout: "List of devices attached\n192.168.1.50:37123\tdevice\n")
        let store = MemoryADBPortStore()
        store.ports["192.168.1.50"] = 40000          // stale port from before a reboot
        var probed: [Int] = []
        let c = ADBClient(executable: "adb", runner: r, store: store,
                          probe: { _, p in probed.append(p); return p == 37123 })
        let reach = c.reach(ip: "192.168.1.50", announce: ADBAnnounce(connect: 37123))
        #expect(reach == .device(port: 37123))
        #expect(probed == [40000, 37123])
        #expect(store.ports["192.168.1.50"] == 37123)
        #expect(r.calls.contains(["-P", "15037", "connect", "192.168.1.50:37123"]))
    }

    @Test func test_reach_unauthorized_and_closed() {
        let r = ADBScriptRunner()
        r.on(["devices"], stdout: "192.168.1.60:5555\tunauthorized\n")
        let store = MemoryADBPortStore()
        let c = ADBClient(executable: "adb", runner: r, store: store, probe: { _, _ in true })
        #expect(c.reach(ip: "192.168.1.60", announce: ADBAnnounce()) == .unauthorized(port: 5555))
        #expect(store.ports["192.168.1.60"] == nil)
        let shut = ADBClient(executable: "adb", runner: r, store: store, probe: { _, _ in false })
        #expect(shut.reach(ip: "192.168.1.60", announce: ADBAnnounce()) == .closed)
    }

    @Test func test_install_split_app_and_failure() {
        let r = ADBScriptRunner()
        r.on(["install-multiple"], stdout: "Success")
        r.on(["install"], stdout: "Failure [INSTALL_FAILED_OLDER_SDK]", exit: 1)
        let store = MemoryADBPortStore(); store.ports["10.0.0.2"] = 5555
        let c = ADBClient(executable: "adb", runner: r, store: store, probe: { _, _ in true })
        #expect(c.install(ip: "10.0.0.2", apks: ["/a/base.apk", "/a/split.apk"]) == nil)
        #expect(r.calls.last == ["-P", "15037", "-s", "10.0.0.2:5555", "install-multiple", "-r",
                                      "/a/base.apk", "/a/split.apk"])
        #expect(c.install(ip: "10.0.0.2", apks: ["/a/x.apk"]) == "This APK needs a newer Android than the TV has.")
    }

    @Test func test_pair_uses_ip_and_pairing_port() {
        let r = ADBScriptRunner()
        r.on(["pair"], stdout: "Successfully paired to 192.168.1.50:41233 [guid=adb-1A2B]")
        let c = ADBClient(executable: "adb", runner: r, store: MemoryADBPortStore(), probe: { _, _ in true })
        #expect(c.pair(ip: "192.168.1.50", port: 41233, code: "123456").ok)
        #expect(r.calls.last == ["-P", "15037", "pair", "192.168.1.50:41233", "123456"])
    }
}

@MainActor @Suite struct ADBControllerTests {

    private func controller(_ r: ADBScriptRunner, store: ADBPortStore = MemoryADBPortStore(),
                            open: Bool = true) -> ADBController {
        ADBController(locate: { "/opt/homebrew/bin/adb" },
                      makeClient: { ADBClient(executable: $0, runner: r, store: store, probe: { _, _ in open }) })
    }

    @Test func test_missing_adb() async {
        let c = ADBController(locate: { nil })
        c.use(ip: "192.168.1.50")
        await c.connect()
        #expect(c.link == .missing)
    }

    @Test func test_connect_one_click_then_now_playing() async {
        let r = ADBScriptRunner()
        r.on(["mdns"], stdout: mdnsSample)
        r.on(["devices"], stdout: devicesSample)
        r.on(["dumpsys media_session"], stdout: dumpsysSample)
        let c = controller(r)
        c.use(ip: "192.168.1.50")
        await c.connect()
        #expect(c.link == .connected(port: 37123))
        await c.settle()
        #expect(c.nowPlaying.title == "Never Gonna Give You Up, Hello, World")
        #expect(r.calls.contains(["-P", "15037", "-s", "192.168.1.50:37123", "shell",
                                        ADBClient.nowPlayingCommand]))
    }

    @Test func test_pairing_code_when_tv_shows_it() async {
        let r = ADBScriptRunner()
        r.on(["mdns"], stdout: mdnsSample)
        let c = controller(r, open: false)              // nothing answers yet
        c.use(ip: "192.168.1.50")
        await c.connect()
        #expect(c.link == .needsCode)
        await c.pair(code: "12345")
        #expect(c.messageIsError == true)
        #expect(!r.calls.contains { $0.contains("pair") })
    }

    @Test func test_debugging_off_and_unauthorized() async {
        let r = ADBScriptRunner()
        let c = controller(r, open: false)
        c.use(ip: "192.168.1.99")
        await c.connect()
        #expect(c.link == .debuggingOff)
        c.use(ip: nil)                                   // stops the watch

        let r2 = ADBScriptRunner()
        r2.on(["devices"], stdout: devicesSample)
        let c2 = controller(r2)
        c2.use(ip: "192.168.1.60")
        await c2.connect()
        #expect(c2.link == .waitingAllow)
        c2.use(ip: nil)                                  // stops the watch
    }

    @Test func test_never_polls_a_tv_that_never_worked() async {
        let r = ADBScriptRunner()
        let c = controller(r)
        c.use(ip: "192.168.1.50")
        c.panelOpen = true
        c.tick(now: Date().addingTimeInterval(120))
        await c.settle()
        #expect(r.calls.isEmpty, "polling would put “Allow debugging?” on the TV")
    }

    @Test func test_poll_cadence_idle_once_a_minute() async {
        let r = ADBScriptRunner()
        r.on(["dumpsys"], stdout: "__UP__\n1 1\n")
        let store = MemoryADBPortStore(); store.ports["192.168.1.50"] = 5555
        let c = controller(r, store: store)
        c.use(ip: "192.168.1.50")                        // worked before: quiet poll
        await c.settle()
        #expect(c.link == .connected(port: 5555))
        let n = r.calls.count
        c.tick(now: Date().addingTimeInterval(20))       // panel closed, idle: not yet
        await c.settle()
        #expect(r.calls.count == n)
        c.tick(now: Date().addingTimeInterval(61))
        await c.settle()
        #expect(r.calls.count > n)
    }

    @Test func test_install_needs_an_apk() async {
        let c = controller(ADBScriptRunner())
        c.use(ip: "192.168.1.50")
        await c.installAPK([URL(fileURLWithPath: "/tmp/movie.mkv")])
        #expect(c.message == "That's not an APK file.")
    }
}
