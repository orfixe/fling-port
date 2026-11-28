// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import CryptoKit
import Foundation
import Testing
@testable import FlingKit

/// Serves canned answers by URL; records which requests were strict (no redirects).
final class FakeHTTP: CatalogHTTP, @unchecked Sendable {
    var pages: [String: Data] = [:]
    var sizes: [String: Int] = [:]
    var strictURLs: [String] = []
    var downloads: [String: Data] = [:]

    func set(_ url: String, _ text: String) { pages[url] = Data(text.utf8) }

    func get(_ url: String, limit: Int, strict: Bool) async throws -> Data {
        if strict { strictURLs.append(url) }
        guard let d = pages[url] else { throw CatalogError(CatalogText.serverSaid(404)) }
        return d
    }

    func headSize(_ url: String) async -> Int { sizes[url] ?? 0 }

    func download(_ url: String, to file: URL, limit: Int, progress: @escaping @Sendable (Int, Int) -> Void) async throws {
        guard let d = downloads[url] else { throw CatalogError(CatalogText.serverSaid(404)) }
        try d.write(to: file)
        progress(d.count, d.count)
    }
}

/// A TV that answers like `adb` would.
final class FakeADB: CatalogADB, @unchecked Sendable {
    var readyError: String?
    var calls: [[String]] = []
    var installOutput = "Performing Streamed Install\nSuccess\n"
    var packages = "package:com.android.settings\npackage:org.videolan.vlc\n"

    func ready(ip: String, connect: Bool) async -> String? { readyError }

    func run(ip: String, args: [String], timeout: TimeInterval) async -> ProcessResult {
        calls.append(args)
        if args.first == "install" { return ProcessResult(exitCode: 0, stdout: installOutput, stderr: "") }
        let cmd = args.last ?? ""
        if cmd.hasPrefix("getprop") {
            return ProcessResult(exitCode: 0, stdout: "arm64-v8a,armeabi-v7a,armeabi\r\n@\r\narm64-v8a\r\n@\r\n12\r\n@\r\n" + packages, stderr: "")
        }
        if cmd.hasPrefix("echo @") {
            return ProcessResult(exitCode: 0, stdout: "@org.videolan.vlc\n    versionName=3.6.0 Beta 1\n", stderr: "")
        }
        if cmd.hasPrefix("pm list packages") { return ProcessResult(exitCode: 0, stdout: packages, stderr: "") }
        return ProcessResult(exitCode: 0, stdout: "", stderr: "")
    }
}

func sha(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

struct CatalogSourcesTests {
    let a = String(repeating: "a", count: 64), b = String(repeating: "b", count: 64)

    func githubRelease(digest: String?, extra: String = "") -> String {
        """
        {"tag_name": "v0.17.0", "html_url": "https://github.com/example/app/releases/tag/v0.17.0", "assets": [
          {"name": "app-arm64.apk", "size": 1000, \(digest.map { "\"digest\": \"sha256:\($0)\"," } ?? "")
           "browser_download_url": "https://github.com/example/app/releases/download/v0.17.0/app-arm64.apk"},
          {"name": "app-arm32.apk", "size": 900, "digest": "sha256:\(b)",
           "browser_download_url": "https://github.com/example/app/releases/download/v0.17.0/app-arm32.apk"}
          \(extra)
        ]}
        """
    }

    let sums = #"{"name": "SHA256SUMS", "browser_download_url": "https://github.com/example/app/releases/download/v0.17.0/SHA256SUMS"}"#
    let api = "https://api.github.com/repos/example/app/releases/latest"
    let assets = ["arm64-v8a": #"app-arm64\.apk"#, "armeabi-v7a": #"app-arm32\.apk"#]

    @Test func testGitHubPicksTheTVProcessorAndDigest() async throws {
        let http = FakeHTTP()
        http.set(api, githubRelease(digest: a))
        let rel = try await GitHubSource("example/app", assets).latest(abis: ["arm64-v8a", "armeabi-v7a"], http: http, progress: nil)
        #expect(rel.version == "0.17.0")
        #expect(rel.name == "app-arm64.apk")
        #expect(rel.sha256 == a)
        #expect(rel.abi == "arm64-v8a")
        #expect(rel.size == 1000)
        let v7 = try await GitHubSource("example/app", assets).latest(abis: ["armeabi-v7a"], http: http, progress: nil)
        #expect(v7.name == "app-arm32.apk")
    }

    @Test func testGitHubChecksumFileMustAgreeWithDigest() async throws {
        let http = FakeHTTP()
        http.set(api, githubRelease(digest: a, extra: "," + sums))
        http.set("https://github.com/example/app/releases/download/v0.17.0/SHA256SUMS", "\(a)  app-arm64.apk\n\(b) *app-arm32.apk\n")
        let ok = try await GitHubSource("example/app", assets, sums: "SHA256SUMS").latest(abis: ["arm64-v8a"], http: http, progress: nil)
        #expect(ok.sha256 == a)
        http.set("https://github.com/example/app/releases/download/v0.17.0/SHA256SUMS", "\(b)  app-arm64.apk\n")
        await assertThrows(CatalogText.sumsDiffer) {
            _ = try await GitHubSource("example/app", self.assets, sums: "SHA256SUMS").latest(abis: ["arm64-v8a"], http: http, progress: nil)
        }
    }

    @Test func testGitHubWithoutChecksumOrWithForeignLinkIsRefused() async {
        let http = FakeHTTP()
        http.set(api, githubRelease(digest: nil))
        await assertThrows(CatalogText.noChecksum) {
            _ = try await GitHubSource("example/app", self.assets).latest(abis: ["arm64-v8a"], http: http, progress: nil)
        }
        http.set(api, githubRelease(digest: a).replacingOccurrences(of: "github.com/example/app/releases/download", with: "evil.example/x"))
        await assertThrows(CatalogText.badLink) {
            _ = try await GitHubSource("example/app", self.assets).latest(abis: ["arm64-v8a"], http: http, progress: nil)
        }
        http.set(api, githubRelease(digest: a))
        await assertThrows(CatalogText.noBuild) {
            _ = try await GitHubSource("example/app", self.assets).latest(abis: ["x86"], http: http, progress: nil)
        }
    }

    let vlcDir = "https://get.videolan.org/vlc-android/last/"
    var vlc: MirrorbitsSource { Catalog.app("vlc")!.source as! MirrorbitsSource }

    @Test func testMirrorbitsNewestFileAndChecksumFromTheOfficialHost() async throws {
        let http = FakeHTTP()
        http.set(vlcDir, """
        <a href="VLC-Android-3.6.2-arm64-v8a.apk">x</a> <a href="VLC-Android-3.6.10-arm64-v8a.apk">x</a>
        <a href="VLC-Android-3.6.10-armeabi-v7a.apk">x</a> <a href="../">up</a> <a href="VLC-Android-3.6.10-arm64-v8a.apk.sha256">s</a>
        """)
        let url = vlcDir + "VLC-Android-3.6.10-arm64-v8a.apk"
        http.set(url + "?sha256", "\(a)  VLC-Android-3.6.10-arm64-v8a.apk\n")
        http.set(url + "?mirrorlist", "<li>Filesize: 51.2 MB (53687091 bytes)</li>")
        let rel = try await vlc.latest(abis: ["arm64-v8a", "armeabi-v7a"], http: http, progress: nil)
        #expect(rel.version == "3.6.10")
        #expect(rel.url == url)
        #expect(rel.sha256 == a)
        #expect(rel.size == 53687091)
        #expect(rel.abi == "arm64-v8a")
        #expect(http.strictURLs.contains(url + "?sha256"))
    }

    @Test func testMirrorbitsChecksumForAnotherFileIsRefused() async {
        let http = FakeHTTP()
        http.set(vlcDir, #"<a href="VLC-Android-3.6.10-arm64-v8a.apk">x</a>"#)
        http.set(vlcDir + "VLC-Android-3.6.10-arm64-v8a.apk?sha256", "\(a)  something-else.apk\n")
        await assertThrows(CatalogText.noChecksum) { _ = try await self.vlc.latest(abis: ["arm64-v8a"], http: http, progress: nil) }
        http.set(vlcDir + "VLC-Android-3.6.10-arm64-v8a.apk?sha256", "not a checksum")
        await assertThrows(CatalogText.noChecksum) { _ = try await self.vlc.latest(abis: ["arm64-v8a"], http: http, progress: nil) }
    }

    @Test func testKodiFolderPerProcessor() async throws {
        let kodi = Catalog.app("kodi")!.source as! MirrorbitsSource
        let http = FakeHTTP()
        let dir = "https://mirrors.kodi.tv/releases/android/arm/"
        http.set(dir, #"<a href="kodi-21.2-Omega-armeabi-v7a.apk">k</a><a href="kodi-21.1-Omega-armeabi-v7a.apk">k</a>"#)
        http.set(dir + "kodi-21.2-Omega-armeabi-v7a.apk?sha256", a)
        let rel = try await kodi.latest(abis: ["armeabi-v7a"], http: http, progress: nil)
        #expect(rel.version == "21.2")
        await assertThrows(CatalogText.noBuild) { _ = try await kodi.latest(abis: ["x86"], http: http, progress: nil) }
    }

    @Test func testTailscale() async throws {
        let http = FakeHTTP()
        let base = "https://pkgs.tailscale.com/stable/"
        http.set(base + "?mode=json", #"{"APKsVersion": "1.90.4", "APKs": {"universal": "tailscale-android-universal-1.90.4.apk"}}"#)
        http.set(base + "tailscale-android-universal-1.90.4.apk.sha256", a + "\n")
        http.sizes[base + "tailscale-android-universal-1.90.4.apk"] = 4321
        let rel = try await TailscaleSource().latest(abis: ["arm64-v8a"], http: http, progress: nil)
        #expect(rel.version == "1.90.4")
        #expect(rel.sha256 == a)
        #expect(rel.size == 4321)
        #expect(Set(http.strictURLs) == [base + "?mode=json", base + "tailscale-android-universal-1.90.4.apk.sha256"])
        http.set(base + "?mode=json", #"{"APKs": {"universal": "../evil.apk"}}"#)
        await assertThrows(CatalogText.noBuild) { _ = try await TailscaleSource().latest(abis: [], http: http, progress: nil) }
    }

    @Test func testCatalogDataIsPortedAsIs() {
        #expect(Catalog.apps.map(\.key) == ["vlc", "kodi", "jellyfin", "moonlight", "localsend", "tailscale", "files", "fdroid"])
        for app in Catalog.apps {
            #expect(!(app.signers.isEmpty), Comment(rawValue: app.key))
            #expect(app.signers.allSatisfy { hex64($0) != nil }, Comment(rawValue: app.key))
            #expect(CatalogInstaller.validPackage(app.pkg), Comment(rawValue: app.key))
        }
        #expect(Catalog.app("fdroid")?.signers == [FDroidIndex.repoCert])
    }

    // MARK: TV side

    @Test func testProbeReadsProcessorAndInstalledApps() async {
        let installer = CatalogInstaller(http: FakeHTTP(), adb: FakeADB())
        let info = await installer.probe(TvInfo(ip: "10.0.0.5", name: "TV", adb: true))
        #expect(info.abis == ["arm64-v8a", "armeabi-v7a"])
        #expect(info.android == "12")
        #expect(info.installed == ["org.videolan.vlc": "3.6.0"])
    }

    @Test func testInstallVerifiesThenInstalls() async throws {
        let cert = Data("jellyfin developer".utf8)
        let apk = FakeAPK.v2(pkg: "org.example.app", versionName: "0.17.0", cert: cert)
        let http = FakeHTTP()
        let api = "https://api.github.com/repos/example/app/releases/latest"
        let url = "https://github.com/example/app/releases/download/v0.17.0/app.apk"
        http.set(api, #"{"tag_name": "v0.17.0", "assets": [{"name": "app.apk", "size": \#(apk.count), "digest": "sha256:\#(sha(apk))", "browser_download_url": "\#(url)"}]}"#)
        http.downloads[url] = apk
        let adb = FakeADB()
        adb.packages = "package:org.example.app\n"
        let app = CatalogApp(key: "ex", title: "Ex", pkg: "org.example.app", purpose: "", license: "", licenseURL: "",
                             homepage: "", source: GitHubSource("example/app", ["": #"app\.apk"#]), signers: [sha(cert)], tile: ("E", "#000"))
        let installer = CatalogInstaller(http: http, adb: adb)
        let v = try await installer.install(app, ip: "10.0.0.5") { _ in }
        #expect(v == "0.17.0")
        #expect(adb.calls.contains { $0.first == "install" && $0[1] == "-r" && $0[2].hasSuffix("/org.example.app.apk") })

        // another signer: nothing reaches the TV
        let other = CatalogApp(key: "ex", title: "Ex", pkg: "org.example.app", purpose: "", license: "", licenseURL: "",
                               homepage: "", source: app.source, signers: [a], tile: ("E", "#000"))
        let adb2 = FakeADB()
        await assertThrows(CatalogText.wrongKey) {
            _ = try await CatalogInstaller(http: http, adb: adb2).install(other, ip: "10.0.0.5") { _ in }
        }
        #expect(!(adb2.calls.contains { $0.first == "install" }))

        // a changed file: checksum stops it
        http.downloads[url] = apk + Data([0])
        let adb3 = FakeADB()
        await assertThrows(CatalogText.truncated) {
            _ = try await CatalogInstaller(http: http, adb: adb3).install(app, ip: "10.0.0.5") { _ in }
        }
        #expect(!(adb3.calls.contains { $0.first == "install" }))
    }

    @Test func testInstallErrorsInPlainWords() {
        #expect(CatalogInstaller.installError("Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package x signatures do not match]") == "the TV didn't install the app: the TV already has this app with a different signature (from Google Play, for example) — remove it on the TV and try again.")
        #expect(CatalogInstaller.installError("adb: failed\nsomething odd\n") == "the TV didn't install the app: something odd")
    }

    @Test func testADBNotReadyStopsBeforeDownloading() async {
        let adb = FakeADB()
        adb.readyError = CatalogInstaller.adbNotConnected
        await assertThrows(CatalogInstaller.adbNotConnected) {
            _ = try await CatalogInstaller(http: FakeHTTP(), adb: adb).install(Catalog.app("vlc")!, ip: "10.0.0.5") { _ in }
        }
    }

    private func assertThrows(_ message: String, sourceLocation: SourceLocation = #_sourceLocation,
                              _ body: () async throws -> Void) async {
        do {
            try await body()
            Issue.record("expected: \(message)", sourceLocation: sourceLocation)
        } catch {
            #expect((error as? CatalogError)?.message == message, sourceLocation: sourceLocation)
        }
    }
}
