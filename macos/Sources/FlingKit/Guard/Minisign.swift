// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import CryptoKit
import Foundation

/// Port (macOS guard): checks minisign signatures — the format of the Fling release key
/// (SECURITY.md → "Is my copy the real Fling?").
///
/// minisign signs with Ed25519. The current format ("ED", what the release tool
/// `fling-sign` writes) signs the BLAKE2b-512 hash of the file; the legacy format ("Ed")
/// signs the file itself. CryptoKit has Ed25519 (Curve25519.Signing) but no BLAKE2b, so
/// BLAKE2b is implemented below (RFC 7693). A second "global" signature covers the
/// signature plus the trusted comment, so the comment ("fling-release version=0.5.8")
/// cannot be swapped either.
public enum Minisign {
    public struct Failure: Error, Equatable, CustomStringConvertible {
        public let description: String
        init(_ d: String) { description = d }
    }

    public struct PublicKey: Equatable {
        public let keyID: [UInt8]       // 8 bytes
        public let key: [UInt8]         // 32 bytes, Ed25519

        /// The base64 line of a `.pub` file, or the whole file (comment line ignored).
        public init(_ text: String) throws {
            let line = text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
                .first { !$0.isEmpty && !$0.hasPrefix("untrusted comment:") } ?? ""
            guard let raw = Data(base64Encoded: line).map(Array.init), raw.count == 42,
                  raw[0] == 0x45, raw[1] == 0x64 else { throw Failure("not a minisign public key") }   // "Ed"
            keyID = Array(raw[2..<10])
            key = Array(raw[10..<42])
        }

        public var keyIDHex: String { keyID.reversed().map { String(format: "%02X", $0) }.joined() }
    }

    public struct Signature {
        public let prehashed: Bool      // "ED" (BLAKE2b-512 of the file) vs legacy "Ed"
        public let keyID: [UInt8]
        public let signature: [UInt8]   // 64
        public let trustedComment: String
        public let globalSignature: [UInt8]

        public init(_ text: String) throws {
            let lines = text.components(separatedBy: "\n").map { $0.hasSuffix("\r") ? String($0.dropLast()) : $0 }
            guard lines.count >= 4, lines[0].hasPrefix("untrusted comment:") else {
                throw Failure("not a minisign signature")
            }
            guard let raw = Data(base64Encoded: lines[1].trimmingCharacters(in: .whitespaces)).map(Array.init),
                  raw.count == 74, raw[0] == 0x45 else { throw Failure("bad signature line") }
            switch raw[1] {
            case 0x44: prehashed = true     // "ED"
            case 0x64: prehashed = false    // "Ed"
            default: throw Failure("unknown signature algorithm")
            }
            keyID = Array(raw[2..<10])
            signature = Array(raw[10..<74])
            let tcPrefix = "trusted comment: "
            guard lines[2].hasPrefix(tcPrefix) else { throw Failure("no trusted comment") }
            trustedComment = String(lines[2].dropFirst(tcPrefix.count))
            guard let g = Data(base64Encoded: lines[3].trimmingCharacters(in: .whitespaces)).map(Array.init),
                  g.count == 64 else { throw Failure("bad global signature") }
            globalSignature = g
        }
    }

    /// Checks `data` against `signature` made by `key`; returns the trusted comment.
    /// Legacy (non-prehashed) signatures are refused unless `allowLegacy`, like
    /// minisign-verify in fling-guard.
    @discardableResult
    public static func verify(_ data: Data, signature: Signature, key: PublicKey,
                              allowLegacy: Bool = false) throws -> String {
        guard signature.keyID == key.keyID else {
            throw Failure("signed with a different key (\(hex(signature.keyID.reversed())), expected \(key.keyIDHex))")
        }
        if !signature.prehashed && !allowLegacy { throw Failure("legacy signature format refused") }
        guard let pk = try? Curve25519.Signing.PublicKey(rawRepresentation: key.key) else {
            throw Failure("bad public key")
        }
        let message: Data = signature.prehashed ? Data(BLAKE2b.hash(data)) : data
        guard pk.isValidSignature(Data(signature.signature), for: message) else {
            throw Failure("the signature does not match")
        }
        let global = Data(signature.signature) + Data(signature.trustedComment.utf8)
        guard pk.isValidSignature(Data(signature.globalSignature), for: global) else {
            throw Failure("the trusted comment's signature does not match")
        }
        return signature.trustedComment
    }

    public static func verify(_ data: Data, signatureText: String, publicKey: String,
                              allowLegacy: Bool = false) throws -> String {
        try verify(data, signature: Signature(signatureText), key: PublicKey(publicKey), allowLegacy: allowLegacy)
    }

    static func hex<S: Sequence>(_ b: S) -> String where S.Element == UInt8 {
        b.map { String(format: "%02x", $0) }.joined()
    }
}

/// The Fling release key (the same one as security/fling-release.pub and fling-guard).
public enum ReleaseKey {
    public static let publicKey = "RWRm6sdJduEyJc2SZt5ee5tDKKaWvAoJcDttUeHJh1wve7upw8c0p5AJ"
    public static let keyID = "2532E17649C7EA66"
}

/// `SHA256SUMS.txt` of a release, after its signature checked out.
public struct ReleaseSums: Equatable {
    public let version: String?            // from "fling-release version=0.5.8"
    public let sha256: [String: String]    // file name → lowercase hex

    /// Verifies the signature with `publicKey` (the release key by default), then parses.
    /// The trusted comment must be a Fling release one: a file signed with the same key
    /// for another purpose is not a release list.
    public init(sums: Data, signature: String, publicKey: String = ReleaseKey.publicKey) throws {
        let tc = try Minisign.verify(sums, signatureText: signature, publicKey: publicKey)
        guard tc.hasPrefix("fling-release") else { throw Minisign.Failure("signed for something else: \(tc)") }
        version = tc.split(separator: " ").first { $0.hasPrefix("version=") }.map { String($0.dropFirst(8)) }
        guard let text = String(data: sums, encoding: .utf8) else { throw Minisign.Failure("list is not UTF-8") }
        sha256 = try Self.parse(text)
    }

    /// `<64 hex>  <name>` per line (`*name` = binary mode, same thing).
    static func parse(_ text: String) throws -> [String: String] {
        var out: [String: String] = [:]
        for line in text.split(whereSeparator: \.isNewline) where !line.trimmingCharacters(in: .whitespaces).isEmpty {
            let parts = line.split(separator: " ", maxSplits: 1, omittingEmptySubsequences: true)
            guard parts.count == 2 else { throw Minisign.Failure("bad line in the list: \(line)") }
            let hash = parts[0].lowercased()
            var name = parts[1].trimmingCharacters(in: .whitespaces)
            if name.hasPrefix("*") { name.removeFirst() }
            guard hash.count == 64, hash.allSatisfy(\.isHexDigit), !name.isEmpty else {
                throw Minisign.Failure("bad line in the list: \(line)")
            }
            out[name] = hash
        }
        return out
    }

    /// nil = fine; otherwise why `file` (checked under the name `name`) doesn't match.
    public func mismatch(file: URL, name: String? = nil) -> String? {
        let n = name ?? file.lastPathComponent
        guard let want = sha256[n] else { return "\(n) is not in the signed list" }
        guard let got = FileHash.sha256(file) else { return "\(n) can't be read" }
        return got == want ? nil : "\(n) is not the signed file (SHA-256 \(got.prefix(12))…, expected \(want.prefix(12))…)"
    }
}

public enum FileHash {
    /// SHA-256 of a file, streamed (lowercase hex), nil if it can't be read.
    public static func sha256(_ url: URL) -> String? {
        guard let h = FileHandle(forReadingAtPath: url.path) else { return nil }
        defer { try? h.close() }
        var hasher = SHA256()
        while true {
            let chunk: Data?
            do { chunk = try h.read(upToCount: 1 << 20) } catch { return nil }
            guard let chunk, !chunk.isEmpty else { break }   // nil or empty = end of file
            hasher.update(data: chunk)
        }
        return Minisign.hex(hasher.finalize())
    }
}

/// BLAKE2b (RFC 7693), unkeyed, 1…64-byte output. Only what minisign needs.
public struct BLAKE2b {
    private static let iv: [UInt64] = [
        0x6a09e667f3bcc908, 0xbb67ae8584caa73b, 0x3c6ef372fe94f82b, 0xa54ff53a5f1d36f1,
        0x510e527fade682d1, 0x9b05688c2b3e6c1f, 0x1f83d9abfb41bd6b, 0x5be0cd19137e2179,
    ]
    private static let sigma: [[Int]] = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
        [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
        [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
        [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
        [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
        [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
        [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
        [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
        [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
        [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
    ]

    private var h: [UInt64]
    private var counter: UInt64 = 0      // bytes compressed so far (inputs < 2^64 bytes)
    private var buffer: [UInt8] = []
    private let outLength: Int

    public init(outputLength: Int = 64) {
        precondition((1...64).contains(outputLength))
        outLength = outputLength
        h = Self.iv
        h[0] ^= 0x0101_0000 ^ UInt64(outputLength)
        buffer.reserveCapacity(128)
    }

    public mutating func update<D: DataProtocol>(_ data: D) {
        for region in data.regions {
            region.withUnsafeBytes { raw in
                var i = 0
                while i < raw.count {
                    // A full buffer is compressed only once more input follows:
                    // the last block must be compressed with the final flag.
                    if buffer.count == 128 {
                        counter &+= 128
                        compress(buffer, last: false)
                        buffer.removeAll(keepingCapacity: true)
                    }
                    let n = min(128 - buffer.count, raw.count - i)
                    buffer.append(contentsOf: raw[i..<(i + n)])
                    i += n
                }
            }
        }
    }

    public mutating func finalize() -> [UInt8] {
        counter &+= UInt64(buffer.count)
        let block = buffer + [UInt8](repeating: 0, count: 128 - buffer.count)
        compress(block, last: true)
        var out: [UInt8] = []
        for w in h { for b in 0..<8 { out.append(UInt8(truncatingIfNeeded: w >> (8 * UInt64(b)))) } }
        return Array(out.prefix(outLength))
    }

    public static func hash<D: DataProtocol>(_ data: D, outputLength: Int = 64) -> [UInt8] {
        var b = BLAKE2b(outputLength: outputLength)
        b.update(data)
        return b.finalize()
    }

    @inline(__always) private static func rotr(_ x: UInt64, _ n: UInt64) -> UInt64 { (x >> n) | (x << (64 - n)) }

    private mutating func compress(_ block: [UInt8], last: Bool) {
        var m = [UInt64](repeating: 0, count: 16)
        for i in 0..<16 {
            var w: UInt64 = 0
            for b in 0..<8 { w |= UInt64(block[i * 8 + b]) << (8 * UInt64(b)) }
            m[i] = w
        }
        var v = h + Self.iv
        v[12] ^= counter
        // v[13] ^= high 64 bits of the counter: always 0 here
        if last { v[14] = ~v[14] }
        func g(_ a: Int, _ b: Int, _ c: Int, _ d: Int, _ x: UInt64, _ y: UInt64) {
            v[a] = v[a] &+ v[b] &+ x; v[d] = Self.rotr(v[d] ^ v[a], 32)
            v[c] = v[c] &+ v[d];      v[b] = Self.rotr(v[b] ^ v[c], 24)
            v[a] = v[a] &+ v[b] &+ y; v[d] = Self.rotr(v[d] ^ v[a], 16)
            v[c] = v[c] &+ v[d];      v[b] = Self.rotr(v[b] ^ v[c], 63)
        }
        for r in 0..<12 {
            let s = Self.sigma[r % 10]
            g(0, 4, 8, 12, m[s[0]], m[s[1]])
            g(1, 5, 9, 13, m[s[2]], m[s[3]])
            g(2, 6, 10, 14, m[s[4]], m[s[5]])
            g(3, 7, 11, 15, m[s[6]], m[s[7]])
            g(0, 5, 10, 15, m[s[8]], m[s[9]])
            g(1, 6, 11, 12, m[s[10]], m[s[11]])
            g(2, 7, 8, 13, m[s[12]], m[s[13]])
            g(3, 4, 9, 14, m[s[14]], m[s[15]])
        }
        for i in 0..<8 { h[i] ^= v[i] ^ v[i + 8] }
    }
}
