// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import CryptoKit
import Foundation
import Testing
@testable import FlingKit

/// Builds small APK-shaped files: a binary AndroidManifest.xml in a ZIP, with an
/// optional APK Signing Block (v2) or v1 JAR signature.
enum FakeAPK {
    static func le16(_ v: Int) -> Data { Data([UInt8(v & 0xFF), UInt8(v >> 8 & 0xFF)]) }
    static func le32(_ v: Int) -> Data { le16(v & 0xFFFF) + le16(v >> 16 & 0xFFFF) }
    static func le64(_ v: Int) -> Data { le32(v & 0xFFFF_FFFF) + le32(v >> 32) }
    static func lp(_ d: Data) -> Data { le32(d.count) + d }

    /// <manifest package=… versionCode=… versionName=…>; versionCode's name comes through
    /// the resource map (an empty string), like real aapt2 output.
    static func cat(_ parts: Data...) -> Data { parts.reduce(Data(), +) }

    static func manifest(pkg: String, versionName: String, versionCode: Int) -> Data {
        let strings = ["", "versionName", "package", "manifest", pkg, versionName]
        var offsets = Data(), body = Data()
        for s in strings {
            offsets += le32(body.count)
            let u = Data(s.utf8)
            body += cat(Data([UInt8(s.count), UInt8(u.count)]), u, Data([0]))
        }
        while body.count % 4 != 0 { body.append(0) }
        let poolHeader = 28
        let pool = cat(le16(0x0001), le16(poolHeader), le32(poolHeader + offsets.count + body.count), le32(strings.count), le32(0), le32(0x100), le32(poolHeader + offsets.count), le32(0), offsets, body)
        let resMap = cat(le16(0x0180), le16(8), le32(12), le32(0x0101021B))
        func attr(_ name: Int, raw: Int, type: Int, data: Int) -> Data {
            cat(le32(0xFFFF_FFFF), le32(name), le32(raw), le16(8), Data([0, UInt8(type)]), le32(data))
        }
        let attrs = attr(0, raw: 0xFFFF_FFFF, type: 0x10, data: versionCode)
            + attr(1, raw: 5, type: 0x03, data: 5) + attr(2, raw: 4, type: 0x03, data: 4)
        let el = cat(le16(0x0102), le16(16), le32(36 + attrs.count), le32(1), le32(0xFFFF_FFFF), le32(0xFFFF_FFFF), le32(3), le16(20), le16(20), le16(3), le16(0), le16(0), le16(0), attrs)
        let content = cat(pool, resMap, el)
        return cat(le16(0x0003), le16(8), le32(8 + content.count), content)
    }

    /// A stored (uncompressed) ZIP; `block` goes between the entries and the central directory.
    static func zip(_ files: [(String, Data)], block: Data = Data()) -> Data {
        var out = Data(), central = Data()
        for (name, data) in files {
            let n = Data(name.utf8)
            let off = out.count
            out += cat(le32(0x04034B50), le16(20), le16(0), le16(0), le32(0), le32(0), le32(data.count), le32(data.count), le16(n.count), le16(0), n, data)
            central += cat(le32(0x02014B50), le16(20), le16(20), le16(0), le16(0), le32(0), le32(0), le32(data.count), le32(data.count), le16(n.count), le16(0), le16(0), le16(0), le16(0), le32(0), le32(off), n)
        }
        out += block
        let cd = out.count
        out += central
        out += cat(le32(0x06054B50), le16(0), le16(0), le16(files.count), le16(files.count), le32(central.count), le32(cd), le16(0))
        return out
    }

    /// An APK Signing Block with one v2 signer whose certificate is `cert`.
    static func v2Block(cert: Data) -> Data {
        let signedData = lp(Data()) + lp(lp(cert)) + lp(Data())
        let signer = lp(signedData) + lp(Data()) + lp(Data())
        let value = lp(lp(signer))
        let pair = le64(4 + value.count) + le32(0x7109871A) + value
        let size = pair.count + 8 + 16
        return le64(size) + pair + le64(size) + Data("APK Sig Block 42".utf8)
    }

    static func v2(pkg: String, versionName: String = "1.2.3", versionCode: Int = 42, cert: Data) -> Data {
        zip([("AndroidManifest.xml", manifest(pkg: pkg, versionName: versionName, versionCode: versionCode))],
            block: v2Block(cert: cert))
    }
}

/// The body throws; with a message, it is that CatalogError.
func expectError<T>(_ message: String?, sourceLocation: SourceLocation = #_sourceLocation, _ body: () throws -> T) {
    do {
        _ = try body()
        Issue.record("expected an error", sourceLocation: sourceLocation)
    } catch {
        if let message { #expect((error as? CatalogError)?.message == message, sourceLocation: sourceLocation) }
    }
}

struct CatalogVerificationTests {
    let cert = Data("pretend this is a developer certificate".utf8)
    var certSHA: String { SHA256.hash(data: cert).map { String(format: "%02x", $0) }.joined() }

    func app(pkg: String = "org.example.app", signers: [String]) -> CatalogApp {
        CatalogApp(key: "x", title: "X", pkg: pkg, purpose: "", license: "", licenseURL: "", homepage: "",
                   source: GitHubSource("example/x", [:]), signers: signers, tile: ("X", "#000000"))
    }

    // MARK: APK inspection

    @Test func testManifestAndV2Signer() throws {
        let info = try APKInspector.info(FakeAPK.v2(pkg: "org.example.app", cert: cert))
        #expect(info.pkg == "org.example.app")
        #expect(info.versionName == "1.2.3")
        #expect(info.versionCode == 42)
        #expect(info.scheme == "v2")
        #expect(info.signers == [certSHA])
    }

    @Test func testV1SignerFromPKCS7() throws {
        let apk = FakeAPK.zip([("AndroidManifest.xml", FakeAPK.manifest(pkg: "org.example.app", versionName: "2.0", versionCode: 7)),
                               ("META-INF/CERT.RSA", CatalogFixtures.testPKCS7)])
        let info = try APKInspector.info(apk)
        #expect(info.scheme == "v1")
        #expect(info.signers == [CatalogFixtures.testCertSHA256])
    }

    @Test func testUnsignedAPKIsRefused() {
        let apk = FakeAPK.zip([("AndroidManifest.xml", FakeAPK.manifest(pkg: "a.b", versionName: "1", versionCode: 1))])
        expectError(CatalogText.unsigned) { try APKInspector.info(apk) }
    }

    @Test func testNotAnAPK() {
        expectError(CatalogText.notAPK) { try APKInspector.info(Data("hello".utf8)) }
        let noManifest = FakeAPK.zip([("classes.dex", Data([1, 2, 3]))], block: FakeAPK.v2Block(cert: cert))
        expectError(CatalogText.notAPK) { try APKInspector.info(noManifest) }
    }

    @Test func testCheckAPKAcceptsThePinnedDeveloper() throws {
        let apk = FakeAPK.v2(pkg: "org.example.app", cert: cert)
        let rel = Release(version: "1.2.3", url: "", name: "", sha256: String(repeating: "0", count: 64))
        #expect(try CatalogInstaller.checkAPK(app(signers: [certSHA]), data: apk, release: rel).pkg == "org.example.app")
    }

    @Test func testCheckAPKRefusesAnotherSigner() {
        let apk = FakeAPK.v2(pkg: "org.example.app", cert: cert)
        let rel = Release(version: "1", url: "", name: "")
        expectError(CatalogText.wrongKey) { try CatalogInstaller.checkAPK(app(signers: [String(repeating: "a", count: 64)]), data: apk, release: rel) }
        expectError(CatalogText.unknownKey) { try CatalogInstaller.checkAPK(app(signers: []), data: apk, release: rel) }
    }

    @Test func testCheckAPKRefusesAnotherPackage() {
        let apk = FakeAPK.v2(pkg: "com.evil.app", cert: cert)
        expectError(CatalogText.otherApp) { try CatalogInstaller.checkAPK(app(signers: [certSHA]), data: apk,
                                                           release: Release(version: "1", url: "", name: "")) }
    }

    @Test func testFDroidIndexSignerCountsOnlyForFDroidApps() throws {
        let apk = FakeAPK.v2(pkg: "org.example.app", cert: cert)
        let rel = Release(version: "1", url: "", name: "", signers: [certSHA])
        let fd = CatalogApp(key: "f", title: "F", pkg: "org.example.app", purpose: "", license: "", licenseURL: "",
                            homepage: "", source: FDroidSource("org.example.app"), signers: [], tile: ("F", "#000"))
        #expect(throws: Never.self) { try CatalogInstaller.checkAPK(fd, data: apk, release: rel) }
        expectError(nil) { try CatalogInstaller.checkAPK(app(signers: []), data: apk, release: rel) }
    }

    @Test func testDownloadChecksum() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let f = dir.appendingPathComponent("a.apk")
        try Data("payload".utf8).write(to: f)
        let good = SHA256.hash(data: Data("payload".utf8)).map { String(format: "%02x", $0) }.joined()
        #expect(throws: Never.self) { try CatalogInstaller.checkDownload(f, release: Release(version: "", url: "", name: "", size: 7, sha256: good)) }
        expectError(CatalogText.damaged) { try CatalogInstaller.checkDownload(f, release: Release(version: "", url: "", name: "", sha256: String(repeating: "b", count: 64))) }
        expectError(nil) { try CatalogInstaller.checkDownload(f, release: Release(version: "", url: "", name: "", size: 8, sha256: good)) }
        expectError(CatalogText.noChecksum) { try CatalogInstaller.checkDownload(f, release: Release(version: "", url: "", name: "")) }
    }

    // MARK: F-Droid entry.jar

    @Test func testRealFDroidEntryJarVerifiesWithThePinnedKey() throws {
        let entry = try FDroidIndex.verifyEntryJar(CatalogFixtures.fdroidEntryJar)
        let ref = try FDroidIndex.indexRef(entry: entry, seenTimestamp: 0)
        #expect(ref.sha256.count == 64)
        #expect(ref.size > 1 << 20)
        #expect(ref.timestamp > 1_700_000_000_000)
    }

    @Test func testRealFDroidEntryJarFailsWithAnotherPin() {
        expectError(CatalogText.fdroidSignature) { try FDroidIndex.verifyEntryJar(CatalogFixtures.fdroidEntryJar, pinned: CatalogFixtures.testCertSHA256) }
    }

    @Test func testTestKeyJarsWithAndWithoutSignedAttributes() throws {
        for jar in [CatalogFixtures.testNoAttrJar, CatalogFixtures.testAttrJar] {
            let entry = try FDroidIndex.verifyEntryJar(jar, pinned: CatalogFixtures.testCertSHA256)
            #expect(intValue(entry["timestamp"]) == 1_700_000_000_000)
            expectError(nil) { try FDroidIndex.verifyEntryJar(jar) }          // not F-Droid's key
        }
    }

    @Test func testTamperedOrPaddedJarIsRefused() {
        for jar in [CatalogFixtures.testTamperedJar, CatalogFixtures.testExtraJar, Data("junk".utf8)] {
            expectError(CatalogText.fdroidSignature) { try FDroidIndex.verifyEntryJar(jar, pinned: CatalogFixtures.testCertSHA256) }
        }
    }

    @Test func testPKCS7OverOtherDataIsRefused() {
        expectError(nil) { try FDroidIndex.verifyPKCS7(CatalogFixtures.testPKCS7, data: Data("other".utf8),
                                                         pinnedCertSHA256: CatalogFixtures.testCertSHA256) }
    }

    @Test func testIndexRefRefusesRollbackAndOddIndexes() throws {
        let sha = String(repeating: "c", count: 64)
        let entry: [String: Any] = ["timestamp": 1000, "index": ["name": "/index-v2.json", "sha256": sha, "size": 10]]
        #expect(try FDroidIndex.indexRef(entry: entry, seenTimestamp: 999).size == 10)
        expectError(CatalogText.fdroidOld) { try FDroidIndex.indexRef(entry: entry, seenTimestamp: 1001) }
        let other: [String: Any] = ["timestamp": 1000, "index": ["name": "/other.json", "sha256": sha, "size": 10]]
        expectError(nil) { try FDroidIndex.indexRef(entry: other, seenTimestamp: 0) }
        let huge: [String: Any] = ["timestamp": 1000, "index": ["name": "/index-v2.json", "sha256": sha, "size": 1 << 30]]
        expectError(nil) { try FDroidIndex.indexRef(entry: huge, seenTimestamp: 0) }
    }

    @Test func testManifestSectionsJoinContinuationLines() {
        let s = FDroidIndex.manifestSections("Manifest-Version: 1.0\r\n\r\nName: entry.js\r\n on\r\nSHA-256-Digest: abc\r\n\r\n")
        #expect(s.count == 2)
        #expect(s[1]["Name"] == "entry.json")
        #expect(s[1]["SHA-256-Digest"] == "abc")
    }

    // MARK: F-Droid index lookup

    static let index = """
    {"repo": {"name": "F-Droid"}, "packages": {
      "org.other": {"versions": {}},
      "org.fdroid.fdroid": {"metadata": {"name": {"en-US": "F-Droid \\"}{\\" test"}}, "versions": {
        "a": {"file": {"name": "/org.fdroid.fdroid_1020050.apk", "sha256": "\(String(repeating: "1", count: 64))", "size": 100},
              "manifest": {"versionName": "1.20.0", "versionCode": 1020050, "signer": {"sha256": ["\(String(repeating: "2", count: 64))"]}}},
        "b": {"file": {"name": "/org.fdroid.fdroid_1021000.apk", "sha256": "\(String(repeating: "3", count: 64))", "size": 200},
              "manifest": {"versionName": "1.21.0-alpha", "versionCode": 1021000}, "releaseChannels": ["Beta"]},
        "c": {"file": {"name": "/org.fdroid.fdroid_1010000.apk", "sha256": "\(String(repeating: "4", count: 64))", "size": 90},
              "manifest": {"versionName": "1.10", "versionCode": 1010000}}
      }},
      "org.native": {"versions": {
        "x": {"file": {"name": "/n_2.apk", "sha256": "\(String(repeating: "5", count: 64))"}, "manifest": {"versionCode": 2, "nativecode": ["x86"]}},
        "y": {"file": {"name": "/n_1.apk", "sha256": "\(String(repeating: "6", count: 64))"}, "manifest": {"versionCode": 1, "nativecode": ["armeabi-v7a", "arm64-v8a"]}}
      }}
    }}
    """

    @Test func testIndexPicksNewestStableVersion() throws {
        let rel = try FDroidSource.release(index: Data(Self.index.utf8), pkg: "org.fdroid.fdroid", abis: ["arm64-v8a"], page: "p")
        #expect(rel.version == "1.20.0")
        #expect(rel.url == "https://f-droid.org/repo/org.fdroid.fdroid_1020050.apk")
        #expect(rel.name == "org.fdroid.fdroid_1020050.apk")
        #expect(rel.sha256 == String(repeating: "1", count: 64))
        #expect(rel.signers == [String(repeating: "2", count: 64)])
        #expect(rel.abi == "")
    }

    @Test func testIndexMatchesTheTVProcessor() throws {
        let rel = try FDroidSource.release(index: Data(Self.index.utf8), pkg: "org.native", abis: ["arm64-v8a"], page: "")
        #expect(rel.name == "n_1.apk")
        #expect(rel.abi == "arm64-v8a")
        expectError(CatalogText.noBuild) { try FDroidSource.release(index: Data(Self.index.utf8), pkg: "org.native", abis: ["mips"], page: "") }
    }

    @Test func testIndexMissingPackage() {
        expectError(CatalogText.fdroidMissing) { try FDroidIndex.package(indexData: Data(Self.index.utf8), pkg: "org.absent") }
        expectError(nil) { try FDroidSource.release(index: Data(Self.index.utf8), pkg: "org.other", abis: [], page: "") }
    }
}
