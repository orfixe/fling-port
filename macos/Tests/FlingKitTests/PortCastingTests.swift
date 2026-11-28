// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: browser bridge protocol, playlists / subtitles / timestamps, the queue's stepping
// rules and the "now on TV" detector. swift-testing, not XCTest: it also runs with the
// Command Line Tools alone (no Xcode), which ship Testing.framework but no XCTest.
import Foundation
import Testing
@testable import FlingKit

@Suite struct BridgeHTTPTests {
    private func req(_ raw: String) throws -> BridgeHTTP.Request {
        guard case .request(let r) = BridgeHTTP.parse(Data(raw.utf8)) else {
            throw BridgeError("not parsed")
        }
        return r
    }

    private func code(_ route: BridgeHTTP.Route) -> Int? {
        if case .reply(let c, _, _) = route { return c }
        return nil
    }

    @Test func parseWaitsForWholeBody() throws {
        let head = "POST /v1/action HTTP/1.1\r\nHost: 127.0.0.1:47613\r\nContent-Length: 9\r\n\r\n"
        #expect(BridgeHTTP.parse(Data((head + "{\"a\":").utf8)) == .incomplete)
        let r = try req(head + "{\"a\":\"b\"}")
        #expect(r.method == "POST")
        #expect(r.path == "/v1/action")
        #expect(r.headers["host"] == "127.0.0.1:47613")
        #expect(r.body.count == 9)
    }

    @Test func parseRefusesBigBody() {
        let raw = "POST /v1/tab HTTP/1.1\r\nContent-Length: 999999\r\n\r\n"
        #expect(BridgeHTTP.parse(Data(raw.utf8)) == .bad(code: 413))
    }

    @Test func badHostIsRefused() throws {
        let r = try req("GET /v1/status HTTP/1.1\r\nHost: evil.example:47613\r\n\r\n")
        #expect(code(BridgeHTTP.route(r, port: 47613)) == 403)
    }

    @Test func webOriginRefusedExtensionAccepted() throws {
        let web = try req("GET /v1/status HTTP/1.1\r\nHost: 127.0.0.1:47613\r\nOrigin: https://evil.example\r\n\r\n")
        #expect(code(BridgeHTTP.route(web, port: 47613)) == 403)
        let ext = try req("GET /v1/status?x=1 HTTP/1.1\r\nHost: localhost:47613\r\nOrigin: chrome-extension://abc\r\n\r\n")
        #expect(BridgeHTTP.route(ext, port: 47613)
                == .dispatch(path: "/v1/status", body: Data(), origin: "chrome-extension://abc"))
        let curl = try req("GET /v1/ping HTTP/1.1\r\nHost: 127.0.0.1:47613\r\n\r\n")
        #expect(BridgeHTTP.route(curl, port: 47613) == .dispatch(path: "/v1/ping", body: Data(), origin: nil))
    }

    @Test func postNeedsCustomHeaderAndJSONObject() throws {
        let base = "POST /v1/action HTTP/1.1\r\nHost: 127.0.0.1:47613\r\nOrigin: moz-extension://u\r\n"
        #expect(code(BridgeHTTP.route(try req(base + "Content-Length: 2\r\n\r\n{}"), port: 47613)) == 403)
        #expect(code(BridgeHTTP.route(try req(base + "X-Fling: 1\r\nContent-Length: 2\r\n\r\n[]"), port: 47613)) == 400)
        #expect(BridgeHTTP.route(try req(base + "X-Fling: 1\r\nContent-Length: 2\r\n\r\n{}"), port: 47613)
                == .dispatch(path: "/v1/action", body: Data("{}".utf8), origin: "moz-extension://u"))
        #expect(code(BridgeHTTP.route(try req(base + "X-Fling: 1\r\nContent-Length: 2\r\n\r\n{}")
                                        .with(path: "/v1/other"), port: 47613)) == 404)
    }

    @Test func preflightOnlyForExtensionsAndPinnedIDs() throws {
        let ok = try req("OPTIONS /v1/action HTTP/1.1\r\nHost: 127.0.0.1:47613\r\nOrigin: safari-web-extension://id1\r\n\r\n")
        #expect(BridgeHTTP.route(ok, port: 47613) == .preflight(origin: "safari-web-extension://id1"))
        #expect(BridgeHTTP.route(ok, port: 47613, allowedIDs: ["id1"]) == .preflight(origin: "safari-web-extension://id1"))
        #expect(code(BridgeHTTP.route(ok, port: 47613, allowedIDs: ["other"])) == 403)
        let web = try req("OPTIONS /v1/action HTTP/1.1\r\nHost: 127.0.0.1:47613\r\nOrigin: https://a.b\r\n\r\n")
        #expect(code(BridgeHTTP.route(web, port: 47613)) == 403)
    }

    @Test func responseHasCORSForExtension() {
        let d = String(decoding: BridgeHTTP.response(code: 200, body: Data("{}".utf8), origin: "chrome-extension://x"),
                       as: UTF8.self)
        #expect(d.hasPrefix("HTTP/1.1 200 OK\r\n"))
        #expect(d.contains("Access-Control-Allow-Origin: chrome-extension://x\r\n"))
        #expect(d.hasSuffix("Content-Length: 2\r\n\r\n{}"))
    }

    /// The real server on a spare port: a status request comes back as the handler's JSON.
    @Test func serverRoundTrip() async throws {
        let port: UInt16 = 47_699
        let bridge = BrowserBridge(port: port) { path, _ in ["ok": true, "path": path] }
        #expect(bridge.start())
        defer { bridge.stop() }
        try await Task.sleep(for: .milliseconds(300))
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/v1/status")!)
        request.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: request)
        #expect((response as? HTTPURLResponse)?.statusCode == 200)
        let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        #expect(body?["path"] as? String == "/v1/status")
    }
}

private extension BridgeHTTP.Request {
    func with(path: String) -> BridgeHTTP.Request {
        BridgeHTTP.Request(method: method, path: path, headers: headers, body: body)
    }
}

@Suite struct MediaFilesTests {
    @Test func kinds() {
        #expect(MediaFiles.kind(ofPath: "/a/Film.MKV") == .video)
        #expect(MediaFiles.kind(ofPath: "song.flac") == .audio)
        #expect(MediaFiles.kind(ofPath: "IMG_1.HEIC") == .image)
        #expect(MediaFiles.kind(ofPath: "list.m3u8") == .playlist)
        #expect(MediaFiles.kind(ofPath: "film.ru.srt") == .subtitles)
        #expect(MediaFiles.kind(ofPath: "notes.txt") == nil)
    }

    @Test func m3uWithTitlesLinksAndRelativeFiles() {
        let text = "#EXTM3U\n#EXTINF:123,First song\nmusic/a.mp3\nhttps://example.com/s.mp3\nsmb://nas/x.mp3\n/abs/b.mp4\nmissing.mp3\n"
        let items = MediaFiles.parsePlaylist(text, ext: "m3u", baseDir: "/lib",
                                             fileExists: { ["/lib/music/a.mp3", "/abs/b.mp4"].contains($0) })
        #expect(items == [MediaItem(src: "/lib/music/a.mp3", title: "First song"),
                          MediaItem(src: "https://example.com/s.mp3", isWeb: true),
                          MediaItem(src: "/abs/b.mp4")])
    }

    @Test func pls() {
        let text = "[playlist]\nFile2=two.mp3\nFile1=http://radio.example/stream.mp3\nTitle1=Radio\nNumberOfEntries=2\n"
        let items = MediaFiles.parsePlaylist(text, ext: "pls", baseDir: "/m", fileExists: { _ in true })
        #expect(items.map(\.src) == ["http://radio.example/stream.mp3", "/m/two.mp3"])
        #expect(items.first?.title == "Radio")
    }

    @Test func srtAndAssToVTT() {
        let srt = "1\r\n00:00:01,500 --> 00:00:03,000\r\nПривет\r\n"
        #expect(MediaFiles.toVTT(srt, ext: "srt") == "WEBVTT\n\n1\n00:00:01.500 --> 00:00:03.000\nПривет\n")
        let ass = """
        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        Dialogue: 0,0:00:02.00,0:00:04.50,Default,,0,0,0,,{\\i1}Hello\\Nworld, again
        """
        #expect(MediaFiles.toVTT(ass, ext: "ass") == "WEBVTT\n\n00:00:02.000 --> 00:00:04.500\nHello\nworld, again\n")
    }

    @Test func windows1251SubtitlesAreDecoded() {
        let data = "Привет, мир".data(using: .windowsCP1251)!
        #expect(MediaFiles.decodeText(data) == "Привет, мир")
    }

    @Test func subtitlesNextToVideoPreferInterfaceLanguage() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        for n in ["Film.mp4", "Film.en.srt", "Film.ru.srt", "Other.srt"] {
            FileManager.default.createFile(atPath: dir.appendingPathComponent(n).path, contents: Data())
        }
        let video = dir.appendingPathComponent("Film.mp4").path
        let name = { (lang: String) in
            MediaFiles.findSubtitles(forVideo: video, language: lang).map { ($0 as NSString).lastPathComponent }
        }
        #expect(name("ru") == "Film.ru.srt")
        #expect(name("en") == "Film.en.srt")
    }

    @Test func folderScanAndExpand() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("CD1"), withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        for n in ["Ep 10.mp4", "Ep 2.mp4", ".hidden.mp4", "notes.txt", "CD1/a.mp3"] {
            FileManager.default.createFile(atPath: dir.appendingPathComponent(n).path, contents: Data())
        }
        let names = MediaFiles.scanFolder(dir.path).map { ($0 as NSString).lastPathComponent }
        #expect(names == ["Ep 2.mp4", "Ep 10.mp4", "a.mp3"])
        let sub = dir.appendingPathComponent("my.srt").path
        FileManager.default.createFile(atPath: sub, contents: Data())
        let r = MediaFiles.expand([dir.appendingPathComponent("Ep 2.mp4").path, sub])
        #expect(r.items.count == 1)
        #expect(r.items.first?.subtitles == sub)
    }

    @Test func timesAndLinkStarts() {
        #expect(MediaFiles.parseTime("90") == 90)
        #expect(MediaFiles.parseTime("1m30s") == 90)
        #expect(MediaFiles.parseTime("1h2m3s") == 3723)
        #expect(MediaFiles.parseTime("01:02:03") == 3723)
        #expect(MediaFiles.parseTime("soon") == nil)
        #expect(MediaFiles.urlStart("https://youtu.be/abc?t=1m5s") == 65)
        #expect(MediaFiles.urlStart("https://example.com/v.mp4#t=42,60") == 42)
        #expect(MediaFiles.urlStart("https://example.com/v.mp4") == nil)
    }

    @Test func resumeKeyIgnoresTimeParams() {
        #expect(MediaFiles.resumeKey("https://www.youtube.com/watch?v=xyz&t=30") == "yt:xyz")
        #expect(MediaFiles.resumeKey("https://e.com/v.mp4?t=30&q=1#t=5") == "https://e.com/v.mp4?q=1")
    }

    @Test func cattArguments() {
        let film = MediaItem(src: "/nowhere/Film.mp4", subtitles: "off")
        #expect(MediaQueue.arguments(for: film, device: "10.0.0.2", start: 125, language: "en")
                == ["-d", "10.0.0.2", "cast", "-l", "Film", "--no-subs", "-t", "125", "/nowhere/Film.mp4"])
        let web = MediaItem(src: "https://e.com/a.mp3", isWeb: true)
        #expect(MediaQueue.arguments(for: web, device: "d", start: nil, language: "en")
                == ["-d", "d", "cast", "-b", "-f", "https://e.com/a.mp3"])
        let song = MediaItem(src: "/m/Song.mp3", title: "My song")
        #expect(MediaQueue.arguments(for: song, device: "d", start: 0.5, language: "en")
                == ["-d", "d", "cast", "--no-subs", "-l", "My song", "/m/Song.mp3"])
    }
}

@Suite struct QueueOrderTests {
    @Test func playsThroughAndEnds() {
        var q = QueueOrder(count: 3)
        #expect(q.current == 0)
        let r1 = q.advance(auto: true, repeatMode: .off, shuffle: false)
        #expect(r1 == .play(fromStart: false))
        let r2 = q.advance(auto: true, repeatMode: .off, shuffle: false)
        #expect(r2 == .play(fromStart: false))
        #expect(q.current == 2)
        let r3 = q.advance(auto: true, repeatMode: .off, shuffle: false)
        #expect(r3 == .end)
    }

    @Test func repeatAllWrapsAndRepeatOneReplays() {
        var q = QueueOrder(count: 2, start: 1)
        let r4 = q.advance(auto: true, repeatMode: .all, shuffle: false)
        #expect(r4 == .play(fromStart: false))
        #expect(q.current == 0)
        let r5 = q.advance(auto: true, repeatMode: .one, shuffle: false)
        #expect(r5 == .play(fromStart: true))
        #expect(q.current == 0)
        let r6 = q.advance(auto: false, repeatMode: .one, shuffle: false)
        #expect(r6 == .play(fromStart: true))
        #expect(q.current == 1)
    }

    @Test func shuffleKeepsTheChosenFirstItem() {
        let q = QueueOrder(count: 10, start: 4, shuffle: true)
        #expect(q.current == 4)
        #expect(Set(q.order) == Set(0..<10))
    }

    @Test func back() {
        var q = QueueOrder(count: 2)
        let r7 = q.back()
        #expect(!r7)
        _ = q.advance(auto: false, repeatMode: .off, shuffle: false)
        let r8 = q.back()
        #expect(r8)
        #expect(q.current == 0)
    }

    @Test func finishedHeuristic() {
        #expect(MediaQueue.looksFinished(position: 3590, duration: 3600))
        #expect(!MediaQueue.looksFinished(position: 1800, duration: 3600))
        #expect(MediaQueue.looksFinished(position: 178, duration: 180))
        #expect(!MediaQueue.looksFinished(position: nil, duration: 180))
    }
}

@Suite struct TrackChangeTests {
    private func st(_ title: String?, _ state: PlaybackState = .playing) -> CastStatus {
        CastStatus(title: title, elapsed: 1, duration: 100, volume: nil, muted: false, state: state)
    }

    @Test func notifiesOnlyOnARealChange() {
        var d = TrackChangeDetector()
        let t0 = Date()
        var seen: [String?] = []
        seen.append(d.feed(device: "tv", status: st("A"), now: t0))          // first sight
        seen.append(d.feed(device: "tv", status: st("A"), now: t0 + 2))      // same
        seen.append(d.feed(device: "tv", status: st(nil, .idle), now: t0 + 4))   // loading the next one
        seen.append(d.feed(device: "tv", status: st("B"), now: t0 + 6))
        seen.append(d.feed(device: "tv", status: st("YouTube"), now: t0 + 8))    // generic
        seen.append(d.feed(device: "other", status: st("C"), now: t0 + 10))      // another TV
        #expect(seen == [nil, nil, nil, "B", nil, nil])
    }

    @Test func longSilenceResets() {
        var d = TrackChangeDetector()
        let t0 = Date()
        _ = d.feed(device: "tv", status: st("A"), now: t0)
        let r10 = d.feed(device: "tv", status: st("B"), now: t0 + 120)
        #expect(r10 == nil)
    }
}

@MainActor @Suite struct PortSettingsTests {
    @Test func resumePositionsOnlyForLongThings() {
        let d = UserDefaults(suiteName: "fling-test-\(UUID().uuidString)")!
        let s = PortSettings(defaults: d)
        s.storePosition("short", position: 100, duration: 300)
        #expect(s.resumePosition("short") == nil)
        s.storePosition("film", position: 1200, duration: 5400)
        #expect(s.resumePosition("film") == 1200)
        s.storePosition("film", position: 5390, duration: 5400)                    // watched
        #expect(s.resumePosition("film") == nil)
        s.resume = false
        s.storePosition("film", position: 1200, duration: 5400)
        #expect(s.resumePosition("film") == nil)
    }
}
