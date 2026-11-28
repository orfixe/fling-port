// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Testing
import Foundation
@testable import FlingKit

/// Port: Smart View for Samsung TVs. Everything here runs against the local
/// simulators in SamsungSimulators.swift (127.0.0.1) — never a real TV.
/// Swift Testing rather than XCTest: it also runs with the Command Line Tools
/// alone (no Xcode), which have no XCTest.
@MainActor
@Suite(.serialized)
struct SamsungTests {

    private func until(_ timeout: TimeInterval = 5, _ cond: () -> Bool) async -> Bool {
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            if cond() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return cond()
    }

    /// A port nothing listens on: a connect there is refused at once.
    private let closedPort = 1

    private func withDefaults<T>(_ body: (UserDefaults) async throws -> T) async rethrows -> T {
        let name = "fling.samsung.tests.\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        defer { d.removePersistentDomain(forName: name) }
        return try await body(d)
    }

    // MARK: - protocol (pure)

    @Test func remoteURLCarriesBase64NameAndToken() throws {
        let url = try #require(SamsungProtocol.remoteURL(host: "192.168.1.60", token: "123", plain: false))
        #expect(url.scheme == "wss")
        #expect(url.port == 8002)
        #expect(url.path == "/api/v2/channels/samsung.remote.control")
        let q = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        #expect(q.first { $0.name == "name" }?.value == Data("Fling (Mac)".utf8).base64EncodedString())
        #expect(q.first { $0.name == "token" }?.value == "123")
        let plain = try #require(SamsungProtocol.remoteURL(host: "10.0.0.2", token: nil, plain: true))
        #expect(plain.scheme == "ws")
        #expect(plain.port == 8001)
        #expect(!plain.absoluteString.contains("token"))
    }

    @Test func messagesMatchTheDesktopBuild() throws {
        let key = try JSONSerialization.jsonObject(with: Data(SamsungProtocol.keyMessage("KEY_UP").utf8)) as? [String: Any]
        let params = key?["params"] as? [String: String]
        #expect(key?["method"] as? String == "ms.remote.control")
        #expect(params?["DataOfCmd"] == "KEY_UP")
        #expect(params?["Cmd"] == "Click")
        #expect(params?["TypeOfRemote"] == "SendRemoteKey")

        let text = try JSONSerialization.jsonObject(with: Data(SamsungProtocol.textMessage("привет").utf8)) as? [String: Any]
        let tp = text?["params"] as? [String: String]
        #expect(tp?["Cmd"] == Data("привет".utf8).base64EncodedString())
        #expect(tp?["TypeOfRemote"] == "SendInputString")

        let browser = SamsungProtocol.launchMessage(appId: "org.tizen.browser", appType: 4, meta: "https://a.b")
        #expect(browser.contains("NATIVE_LAUNCH"))
        #expect(SamsungProtocol.launchMessage(appId: "111299001912").contains("DEEP_LINK"))
    }

    @Test func parseEvents() {
        #expect(SamsungProtocol.parse(#"{"event":"ms.channel.connect","data":{"token":12345678}}"#)
                == .connected(token: "12345678"))
        #expect(SamsungProtocol.parse(#"{"event":"ms.channel.connect","data":{}}"#) == .connected(token: nil))
        #expect(SamsungProtocol.parse(#"{"event":"ms.channel.unauthorized"}"#) == .unauthorized)
        #expect(SamsungProtocol.parse(#"{"event":"ed.installedApp.get","data":{"data":[{"appId":"1","name":"A","app_type":4}]}}"#)
                == .installedApps([SamsungApp(name: "A", id: "1", type: 4)]))
        #expect(SamsungProtocol.parse("garbage") == .other)
    }

    @Test func androidKeysMapToSamsungKeys() {
        let k = SamsungProtocol.androidKeys
        #expect(k[ATVKeyCode.dpadUp] == "KEY_UP")
        #expect(k[ATVKeyCode.dpadCenter] == "KEY_ENTER")
        #expect(k[ATVKeyCode.back] == "KEY_RETURN")
        #expect(k[ATVKeyCode.home] == "KEY_HOME")
        #expect(k[ATVKeyCode.volumeMute] == "KEY_MUTE")
        #expect(k[ATVKeyCode.volumeUp] == "KEY_VOLUP")
        #expect(k[ATVKeyCode.power] == "KEY_POWER")
        #expect(k[7] == "KEY_0")
        #expect(k[16] == "KEY_9")
    }

    @Test func appLookupPrefersTheTVsOwnList() {
        let installed = [SamsungApp(name: "YouTube", id: "tv-own-id", type: 2)]
        #expect(SamsungProtocol.app(named: "YouTube", installed: installed)?.id == "tv-own-id")
        #expect(SamsungProtocol.app(named: "Netflix", installed: installed)?.id == "3201907018807")
        #expect(SamsungProtocol.app(named: "Tubi", installed: installed) == nil)
        for app in TVApp.catalog where app.name != "Tubi" {
            #expect(SamsungProtocol.app(named: app.name, installed: []) != nil, "\(app.name)")
        }
    }

    @Test func youtubeIDs() {
        #expect(SamsungProtocol.youtubeID("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1") == "dQw4w9WgXcQ")
        #expect(SamsungProtocol.youtubeID("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ")
        #expect(SamsungProtocol.youtubeID("https://youtube.com/shorts/abcdefghijk") == "abcdefghijk")
        #expect(SamsungProtocol.youtubeID("https://www.youtube.com/") == nil)
    }

    @Test func deviceInfoParse() throws {
        let tv = try #require(SamsungProtocol.deviceInfo(json: Data(FakeTVDocs.apiV2().utf8), ip: "10.0.0.5"))
        #expect(tv.name == "[TV] Living room & kitchen")
        #expect(tv.model == "QE55Q80TAU")
        #expect(tv.mac == "aa:bb:cc:dd:ee:ff")
        #expect(tv.tokenKey == "aa:bb:cc:dd:ee:ff")
        #expect(tv.isOn)
        #expect(tv.device.model == "Samsung QE55Q80TAU")
        #expect(SamsungProtocol.deviceInfo(json: Data("{}".utf8), ip: "x") == nil)
    }

    @Test func ssdpAnswerParse() throws {
        let a = try #require(SamsungProtocol.parseSSDP(
            "HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\nLocation: http://1.2.3.4:9197/dmr\r\n\r\n"))
        #expect(a.isRenderer)
        #expect(a.maybeSamsung)
        #expect(a.location == "http://1.2.3.4:9197/dmr")
        let other = try #require(SamsungProtocol.parseSSDP("HTTP/1.1 200 OK\r\nST: upnp:rootdevice\r\nSERVER: Linux\r\n\r\n"))
        #expect(!other.maybeSamsung)
        #expect(SamsungProtocol.parseSSDP("M-SEARCH * HTTP/1.1\r\n\r\n") == nil)
    }

    @Test func magicPacket() throws {
        let p = try #require(SamsungProtocol.magicPacket(mac: "aa:bb:cc:dd:ee:ff"))
        #expect(p.count == 102)
        #expect([UInt8](p.prefix(6)) == [UInt8](repeating: 0xff, count: 6))
        #expect([UInt8](p[6..<12]) == [0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff])
        #expect(SamsungProtocol.magicPacket(mac: "nope") == nil)
    }

    @Test func dlnaPieces() throws {
        let base = URL(string: "http://10.0.0.5:9197/dmr")!
        #expect(SamsungDLNA.controlURL(description: Data(FakeTVDocs.description.utf8), base: base)?.absoluteString
                == "http://10.0.0.5:9197/upnp/control/AVTransport1")
        let env = SamsungDLNA.envelope(action: "Play", args: "<Speed>1</Speed>")
        #expect(env.contains("<u:Play xmlns:u=\"urn:schemas-upnp-org:service:AVTransport:1\"><InstanceID>0</InstanceID><Speed>1</Speed></u:Play>"))
        let args = SamsungDLNA.setURIArgs(url: "http://h/a.mp4?x=1&y=2", title: "A & B", mime: "video/mp4")
        #expect(args.hasPrefix("<CurrentURI>http://h/a.mp4?x=1&amp;y=2</CurrentURI>"))
        #expect(args.contains("&lt;DIDL-Lite"))           // metadata is escaped inside the envelope
        #expect(SamsungDLNA.mime(for: "http://h/song.mp3") == "audio/mpeg")
        #expect(SamsungDLNA.mime(for: "http://h/live.m3u8") == "application/vnd.apple.mpegurl")
    }

    @Test func kindSorting() {
        #expect(DeviceKind.castKind(model: "Chromecast") == .chromecast)
        #expect(DeviceKind.castKind(model: "Google TV Streamer") == .androidTV)
        #expect(DeviceKind.androidTV < DeviceKind.chromecast)
        #expect(DeviceKind.chromecast < DeviceKind.samsung)
    }

    // MARK: - the remote against the simulator

    @Test func remoteFallsBackToPlainAndKeepsTheToken() async throws {
        let sim = try FakeSamsungRemoteServer(mode: .allow(token: "tok-1"))
        defer { sim.stop() }
        let tokens = MemorySamsungTokens()
        let remote = SamsungRemote(tokens: tokens, tlsPort: closedPort, plainPort: Int(sim.port))
        let tv = SamsungTV(ip: "127.0.0.1", name: "Sim", model: "Q", mac: "aa:bb:cc:dd:ee:ff")
        remote.connect(tv)
        let ready = await until { remote.state == .ready }
        #expect(ready, "state: \(remote.state)")
        #expect(tokens.tokens["aa:bb:cc:dd:ee:ff"] == "tok-1")

        #expect(remote.key("KEY_UP"))
        #expect(remote.text("hello"))
        #expect(remote.key("KEY_ENTER"))
        let got = await until { sim.received.get().contains { $0.contains("KEY_ENTER") } }
        #expect(got)
        let msgs = sim.received.get()
        #expect(msgs.contains { $0.contains("ed.installedApp.get") })
        #expect(msgs.contains { $0.contains("KEY_UP") })
        #expect(msgs.contains { $0.contains(Data("hello".utf8).base64EncodedString()) })
        // typed text is closed before Enter runs it
        let end = msgs.firstIndex { $0.contains("SendInputEnd") }
        let enter = msgs.firstIndex { $0.contains("KEY_ENTER") }
        #expect(end != nil)
        if let end, let enter { #expect(end < enter) }
        // the TV's own app list arrived
        let apps = await until { remote.apps.contains { $0.name == "Tubi" } }
        #expect(apps)
        remote.close()
        #expect(remote.state == .off)
        #expect(!remote.key("KEY_UP"))
    }

    @Test func remoteWaitsForAllowThenBecomesReady() async throws {
        let sim = try FakeSamsungRemoteServer(mode: .silent)
        defer { sim.stop() }
        let remote = SamsungRemote(tokens: MemorySamsungTokens(), tlsPort: closedPort, plainPort: Int(sim.port),
                                   waitAfterOpen: .milliseconds(300))
        remote.connect(SamsungTV(ip: "127.0.0.1", name: "Sim", model: "", mac: ""))
        let waiting = await until { remote.state == .waiting }
        #expect(waiting, "state: \(remote.state)")
        sim.allowAll(token: "t2")
        let ready = await until { remote.state == .ready }
        #expect(ready)
        remote.close()
    }

    @Test func remoteDenied() async throws {
        let sim = try FakeSamsungRemoteServer(mode: .deny)
        defer { sim.stop() }
        let remote = SamsungRemote(tokens: MemorySamsungTokens(), tlsPort: closedPort, plainPort: Int(sim.port))
        remote.connect(SamsungTV(ip: "127.0.0.1", name: "Sim", model: "", mac: ""))
        let denied = await until { remote.state == .denied }
        #expect(denied, "state: \(remote.state)")
    }

    @Test func remoteErrorWhenNothingAnswers() async {
        let remote = SamsungRemote(tokens: MemorySamsungTokens(), tlsPort: closedPort, plainPort: closedPort)
        remote.connect(SamsungTV(ip: "127.0.0.1", name: "Nobody", model: "", mac: ""))
        let failed = await until { remote.state == .error }
        #expect(failed, "state: \(remote.state)")
    }

    // MARK: - discovery and DLNA against the simulator

    @Test func ssdpDiscoveryFindsTheSimulatedTV() async throws {
        let http = try FakeHTTPServer { req in
            req.path == "/api/v2/" ? (200, FakeTVDocs.apiV2()) : (404, "")
        }
        defer { http.stop() }
        let ssdp = try FakeSSDPResponder(location: "http://127.0.0.1:\(http.port)/dmr")
        defer { ssdp.stop() }
        let found = await SamsungDiscovery.discover(timeout: 1, target: ("127.0.0.1", ssdp.port),
                                                    apiPort: Int(http.port))
        #expect(ssdp.searches.get() >= 2)
        #expect(found.tvs.map(\.ip) == ["127.0.0.1"])
        #expect(found.tvs.first?.name == "[TV] Living room & kitchen")
        #expect(found.dlna["127.0.0.1"] == "http://127.0.0.1:\(http.port)/dmr")
    }

    @Test func dlnaPlaySendsStopSetURIPlay() async throws {
        let http = try FakeHTTPServer { req in
            if req.method == "GET", req.path == "/dmr" { return (200, FakeTVDocs.description) }
            if req.method == "POST" { return (200, "<ok/>") }
            return (404, "")
        }
        defer { http.stop() }
        let control = try #require(await SamsungDLNA.findControl(ip: "127.0.0.1", location: nil, port: Int(http.port)))
        #expect(control.path == "/upnp/control/AVTransport1")
        try await SamsungDLNA.play(control, url: "http://example.invalid/v.mp4", title: "V", mime: "video/mp4")
        let actions = http.requests.get().compactMap { $0.headers["soapaction"] }
        #expect(actions == ["Stop", "SetAVTransportURI", "Play"].map {
            "\"urn:schemas-upnp-org:service:AVTransport:1#\($0)\""
        })
        #expect(http.requests.get().contains { $0.body.contains("http://example.invalid/v.mp4") })
    }

    // MARK: - AppState with a Samsung TV selected

    @Test func appStateRoutesTheRemoteAppsAndCastsToASamsung() async throws {
        let sim = try FakeSamsungRemoteServer(mode: .allow(token: "tok"))
        defer { sim.stop() }
        let http = try FakeHTTPServer { req in
            if req.method == "GET", req.path == "/dmr" { return (200, FakeTVDocs.description) }
            return req.method == "POST" ? (200, "<ok/>") : (404, "")
        }
        defer { http.stop() }
        try await withDefaults { defaults in
            let tv = SamsungTV(ip: "127.0.0.1", name: "Sim TV", model: "QE55", mac: "aa:bb:cc:dd:ee:ff")
            let dmr = "http://127.0.0.1:\(http.port)/dmr"
            let hub = SamsungHub(
                remote: SamsungRemote(tokens: MemorySamsungTokens(), tlsPort: closedPort, plainPort: Int(sim.port)),
                defaults: defaults,
                discover: { .init(tvs: [tv], dlna: ["127.0.0.1": dmr]) },
                probe: { _ in nil })

            let runner = SilentRunner()
            let state = AppState(catt: CattClient(executable: "/usr/bin/catt", runner: runner),
                                 browsers: BrowserReader(runner: runner))
            state.enableSamsung(hub)
            await state.refresh()
            #expect(state.devices.map(\.ip) == ["127.0.0.1"])
            #expect(state.isSamsungSelected)
            let selected = try #require(state.selectedDevice)
            #expect(state.kind(of: selected) == .samsung)
            #expect(state.chooserLabel(selected) == "Sim TV — Samsung")
            let paired = await until { state.tvPaired }
            #expect(paired, "the remote buttons show once the TV lets Fling in")

            await state.pressTVKey(ATVKeyCode.dpadLeft)
            await state.launchTVApp(TVApp.catalog[0])                       // YouTube
            await state.sendTVText("news")
            state.tab = TabRef(url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", title: "Y", browser: .chrome)
            await state.castCurrentTab()
            #expect(state.lastError == nil)
            let sent = await until { sim.received.get().contains { $0.contains("v=dQw4w9WgXcQ") } }
            #expect(sent)
            let msgs = sim.received.get()
            #expect(msgs.contains { $0.contains("KEY_LEFT") })
            #expect(msgs.contains { $0.contains("111299001912") })
            #expect(msgs.contains { $0.contains(Data("news".utf8).base64EncodedString()) })

            // a direct media link goes over DLNA
            state.tab = TabRef(url: "http://example.invalid/movie.mp4", title: "M", browser: .chrome)
            await state.castCurrentTab()
            #expect(state.lastError == nil)
            #expect(http.requests.get().contains { $0.headers["soapaction"]?.contains("#Play") == true })

            // voice isn't there on Samsung; it says so instead of failing quietly
            await state.toggleVoiceSearch()
            #expect(state.lastError == SamsungText.noVoice)
            #expect(!state.tvVoiceActive)
        }
    }

    @Test func knownSamsungTVStaysListedWhenItDoesntAnswer() async {
        await withDefaults { d in
            let tv = SamsungTV(ip: "10.9.9.9", name: "Bedroom", model: "", mac: "")
            let first = SamsungHub(remote: SamsungRemote(tokens: MemorySamsungTokens()), defaults: d,
                                   discover: { .init(tvs: [tv], dlna: [:]) }, probe: { _ in nil })
            _ = await first.scan()
            let later = SamsungHub(remote: SamsungRemote(tokens: MemorySamsungTokens()), defaults: d,
                                   discover: { .init(tvs: [], dlna: [:]) }, probe: { _ in nil })
            let list = await later.scan()
            #expect(list.map(\.name) == ["Bedroom"])
            #expect(list.first?.isOn == false)
        }
    }
}

/// catt / osascript stand-in: every command "succeeds" with no output.
final class SilentRunner: ProcessRunning, @unchecked Sendable {
    func run(_ executable: String, _ args: [String], timeout: TimeInterval) throws -> ProcessResult {
        ProcessResult(exitCode: 0, stdout: "", stderr: "", timedOut: false)
    }
}
