// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
import CryptoKit
import Foundation
import Testing
@testable import FlingKit

/// All guard tests in one serialized suite: the watchdog tests stop child processes of the
/// test runner, and one test switches the shared guard to enforce for a moment.
@Suite(.serialized) struct GuardSuite {}

/// Port (macOS guard): minisign / BLAKE2b, with known vectors and a throw-away key.
extension GuardSuite {
@Suite final class GuardMinisignTests {

    private func hex(_ b: [UInt8]) -> String { b.map { String(format: "%02x", $0) }.joined() }

    // MARK: BLAKE2b (RFC 7693 appendix A and the reference test vectors)

    @Test func test_blake2b_known_vectors() {
        #expect(hex(BLAKE2b.hash(Data())) == "786a02f742015903c6c6fd852552d272912f4740e15847618a86e217f71f5419d25e1031afee585313896444934eb04b903a685b1448b755d56f701afe9be2ce")
        #expect(hex(BLAKE2b.hash(Data("abc".utf8))) == "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d17d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923")
        #expect(hex(BLAKE2b.hash(Data("The quick brown fox jumps over the lazy dog".utf8))) == "a8add4bdddfd93e4877d2746e62817b116364a1fa7bc148d95090bc7333b3673f82401cf7aa2e4cb1ecd90296e3f14cb5413f8ed77be73045b13914cdcd6a918")
    }

    @Test func test_blake2b_streaming_equals_one_shot_across_block_boundaries() {
        let data = Data((0..<1000).map { UInt8(truncatingIfNeeded: $0 &* 7) })
        for len in [0, 1, 127, 128, 129, 255, 256, 257, 1000] {
            let part = data.prefix(len)
            let one = BLAKE2b.hash(part)
            for chunk in [1, 13, 64, 128, 200] {
                var b = BLAKE2b()
                var i = part.startIndex
                while i < part.endIndex {
                    let j = min(i + chunk, part.endIndex)
                    b.update(part[i..<j])
                    i = j
                }
                #expect(b.finalize() == one, "len \(len) chunk \(chunk)")
            }
        }
    }

    // MARK: minisign test vectors (jedisct1/rust-minisign-verify)

    private let vectorKey = "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"

    @Test func test_legacy_vector() throws {
        let sig = """
        untrusted comment: signature from minisign secret key
        RWQf6LRCGA9i59SLOFxz6NxvASXDJeRtuZykwQepbDEGt87ig1BNpWaVWuNrm73YiIiJbq71Wi+dP9eKL8OC351vwIasSSbXxwA=
        trusted comment: timestamp:1555779966\tfile:test
        QtKMXWyYcwdpZAlPF7tE2ENJkRd1ujvKjlj1m9RtHTBnZPa5WKU5uWRs5GoP5M/VqE81QFuMKI5k/SfNQUaOAA==
        """
        let tc = try Minisign.verify(Data("test".utf8), signatureText: sig, publicKey: vectorKey, allowLegacy: true)
        #expect(tc == "timestamp:1555779966\tfile:test")
        #expect(throws: (any Error).self) { try Minisign.verify(Data("Test".utf8), signatureText: sig, publicKey: vectorKey, allowLegacy: true) }
        // the release check never accepts the legacy format
        #expect(throws: (any Error).self) { try Minisign.verify(Data("test".utf8), signatureText: sig, publicKey: vectorKey) }
    }

    @Test func test_prehashed_vector() throws {
        let sig = """
        untrusted comment: signature from minisign secret key
        RUQf6LRCGA9i559r3g7V1qNyJDApGip8MfqcadIgT9CuhV3EMhHoN1mGTkUidF/z7SrlQgXdy8ofjb7bNJJylDOocrCo8KLzZwo=
        trusted comment: timestamp:1556193335\tfile:test
        y/rUw2y8/hOUYjZU71eHp/Wo1KZ40fGy2VJEDl34XMJM+TX48Ss/17u3IvIfbVR1FkZZSNCisQbuQY+bHwhEBg==
        """
        #expect(try Minisign.verify(Data("test".utf8), signatureText: sig, publicKey: vectorKey) == "timestamp:1556193335\tfile:test")
        #expect(throws: (any Error).self) { try Minisign.verify(Data("tesT".utf8), signatureText: sig, publicKey: vectorKey) }
        // a swapped trusted comment breaks the global signature
        let forged = sig.replacingOccurrences(of: "file:test", with: "file:evil")
        #expect(throws: (any Error).self) { try Minisign.verify(Data("test".utf8), signatureText: forged, publicKey: vectorKey) }
        // another key
        #expect(throws: (any Error).self) { try Minisign.verify(Data("test".utf8), signatureText: sig, publicKey: ReleaseKey.publicKey) }
    }

    // MARK: the real Fling 0.5.8 release list, signed with the release key

    static let releaseSums = """
    0793cb0afc06bff9f3b6382736285b60e8b0300b61f85f7c83300ae8d57e0fbe  Fling
    5639fafdac573aeccba51ecd7e333195e7393c68d87e416073ebc73a0b28f313  Fling.apk
    25a202ddd659f0950340e6601c172ac0fb51ecf1a828f4590662bfe491e9eb34  Fling.app.zip
    7bbcb59265749995fde11b7a165172e9f2808c9d445919cf9533f121372883ce  fling-chrome-0.5.8.zip
    ab86f931b3edc62c66cd2ea2b94d998201e9f0c6688d0fdc783f3bc8b6a8433f  fling-firefox-0.5.8.zip
    c57d627eebe3a1c1b1532a869609ac90197ace12fde54fc7b934ce2ce8c77e1e  fling-safari-0.5.8.zip
    896d050b48b78d8f9efd144d08db940a3285838d22c724fcf7d32db212366390  Fling-Setup-0.5.8.exe
    a254cfe11aeeca786801286b1d827e3b43191c618ebca69eb6b43951a78a5850  Info.plist
    90ab0c41953e0f852dffe73068d4d14e57fa8ac7af895ef7d4f050f7e0bd20e9  install.sh

    """
    static let releaseSig = """
    untrusted comment: Fling release signature
    RURm6sdJduEyJXpnY4ckiXFhqJAjtNi5y21USNHmKrtVlrrK9CXQlZxgCcEZrOL0Xgw7mJAqfsYVARLUm1EZZVdie+974Cx5fwY=
    trusted comment: fling-release version=0.5.8
    i/Y9u/Ch6/x3W3uI3sTKm8uzoTGauMZXcsJwDSlHol46AVDhRjnSKseGzRs/QA0rnVO6eGKW0uHPiPUkHK2aDA==

    """

    @Test func test_real_release_list_verifies_with_the_release_key() throws {
        let list = try ReleaseSums(sums: Data(Self.releaseSums.utf8), signature: Self.releaseSig)
        #expect(list.version == "0.5.8")
        #expect(list.sha256["Fling"] == "0793cb0afc06bff9f3b6382736285b60e8b0300b61f85f7c83300ae8d57e0fbe")
        #expect(list.sha256.count == 9)
        #expect(try Minisign.PublicKey(ReleaseKey.publicKey).keyIDHex == ReleaseKey.keyID)
    }

    @Test func test_changed_release_list_is_refused() {
        let evil = Self.releaseSums.replacingOccurrences(of: "0793cb0a", with: "1793cb0a")
        #expect(throws: (any Error).self) { try ReleaseSums(sums: Data(evil.utf8), signature: Self.releaseSig) }
    }

    @Test func test_file_mismatch_against_the_list() throws {
        let list = try ReleaseSums(sums: Data(Self.releaseSums.utf8), signature: Self.releaseSig)
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let f = dir.appendingPathComponent("Fling")
        try Data("not the program".utf8).write(to: f)
        #expect(list.mismatch(file: f) != nil)
        #expect(list.mismatch(file: dir.appendingPathComponent("unknown")) != nil)
    }

    // MARK: throw-away key (made here, never stored)

    private func makeKey() -> (Curve25519.Signing.PrivateKey, [UInt8], String) {
        let sk = Curve25519.Signing.PrivateKey()
        let id: [UInt8] = (0..<8).map { _ in UInt8.random(in: 0...255) }
        let pk = Data([0x45, 0x64] + id + Array(sk.publicKey.rawRepresentation)).base64EncodedString()
        return (sk, id, "untrusted comment: minisign public key\n\(pk)\n")
    }

    private func sign(_ data: Data, _ sk: Curve25519.Signing.PrivateKey, _ id: [UInt8], tc: String, prehash: Bool = true) throws -> String {
        let msg = prehash ? Data(BLAKE2b.hash(data)) : data
        let sig = try sk.signature(for: msg)
        let line = Data([0x45, prehash ? 0x44 : 0x64] + id + Array(sig)).base64EncodedString()
        let global = try sk.signature(for: sig + Data(tc.utf8)).base64EncodedString()
        return "untrusted comment: test\n\(line)\ntrusted comment: \(tc)\n\(global)\n"
    }

    @Test func test_throwaway_key_roundtrip_and_release_rules() throws {
        let (sk, id, pub) = makeKey()
        let sums = Data("\(String(repeating: "a", count: 64))  Fling\n".utf8)
        let good = try sign(sums, sk, id, tc: "fling-release version=9.9.9")
        let list = try ReleaseSums(sums: sums, signature: good, publicKey: pub)
        #expect(list.version == "9.9.9")
        // signed with this key, but not as a release list
        let other = try sign(sums, sk, id, tc: "something else")
        #expect(throws: (any Error).self) { try ReleaseSums(sums: sums, signature: other, publicKey: pub) }
        // a valid signature by a key that is not the release key
        #expect(throws: (any Error).self) { try ReleaseSums(sums: sums, signature: good) }
        // big data, prehashed
        let big = Data((0..<300_000).map { UInt8(truncatingIfNeeded: $0) })
        #expect(throws: Never.self) { try Minisign.verify(big, signatureText: sign(big, sk, id, tc: "t"), publicKey: pub) }
    }

    @Test func test_cli_verify_release() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let sums = dir.appendingPathComponent("SHA256SUMS.txt")
        try Data(Self.releaseSums.utf8).write(to: sums)
        try Data(Self.releaseSig.utf8).write(to: dir.appendingPathComponent("SHA256SUMS.txt.minisig"))
        var out: [String] = []
        #expect(GuardCLI.handle(["Fling", "--verify-release", sums.path], out: { out.append($0) }, err: { _ in }) == 0)
        #expect(out == ["OK version=0.5.8"])
        let fake = dir.appendingPathComponent("Info.plist")
        try Data("<plist/>".utf8).write(to: fake)
        #expect(GuardCLI.handle(["Fling", "--verify-release", sums.path, fake.path], out: { _ in }, err: { _ in }) == 1)
        #expect(GuardCLI.handle(["Fling"]) == nil)
        #expect(GuardCLI.handle(["Fling", "--preview-panel"]) == nil)
    }
}
}
