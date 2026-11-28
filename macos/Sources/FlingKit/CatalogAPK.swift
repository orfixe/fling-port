// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: the Catalog's APK inspection (desktop/fling_catalog.py), without aapt —
// package name, version and signer certificates of an APK, plus the small DER and
// ZIP readers that the F-Droid index check needs too.
import Compression
import CryptoKit
import Foundation

/// A failure the user should read (English; the UI translates it with tr()).
public struct CatalogError: Error, LocalizedError, Equatable, Sendable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }
}

public enum CatalogText {
    public static let notAPK = "the file doesn't look like an Android app (APK)"
    public static let unsigned = "the APK isn't signed — not installing"
    public static let noManifest = "the APK has no app description (AndroidManifest)"
    public static let redirected = "the official server redirected the checksum request — cancelled"
    public static let noBuild = "the publisher has no build for this TV's processor"
    public static let sumsDiffer = "the publisher's checksums don't match — install cancelled"
    public static let badLink = "the download link points somewhere else — cancelled"
    public static let noChecksum = "the publisher provides no checksum — not installing"
    public static let fdroidSignature = "the F-Droid catalogue signature doesn't match — cancelled"
    public static let fdroidOld = "F-Droid sent an outdated catalogue — cancelled"
    public static let fdroidDamaged = "the F-Droid catalogue is damaged: the checksum didn't match"
    public static let fdroidMissing = "this app isn't in the F-Droid catalogue"
    public static let httpsOnly = "downloads are allowed over HTTPS only"
    public static let redirectNotHTTPS = "the server redirected the download away from HTTPS — cancelled"
    public static let tooBig = "the server's response is too large"
    public static let archiveTooBig = "the archive is suspiciously large — cancelled"
    public static let truncated = "the download was interrupted — press again"
    public static let damaged = "the download is damaged: the checksum didn't match"
    public static let offline = "can't reach the download server"
    public static let otherApp = "the file holds a different app — install cancelled"
    public static let unknownKey = "the developer's key is unknown — not installing"
    public static let wrongKey = "the app isn't signed with the developer's key — install cancelled"
    public static func serverSaid(_ code: Int) -> String { "the server responded with code \(code)" }
}

// MARK: - little-endian / DER helpers (all offsets relative to the start of `d`)

extension Data {
    /// A copy that starts at index 0 (slices keep their parent's indices).
    var rebased: Data { Data(self) }

    func u8(_ i: Int) -> Int { Int(self[startIndex + i]) }
    func u16(_ i: Int) -> Int { u8(i) | u8(i + 1) << 8 }
    func u32(_ i: Int) -> Int { u16(i) | u16(i + 2) << 16 }
    func u64(_ i: Int) -> UInt64 { UInt64(u32(i)) | UInt64(u32(i + 4)) << 32 }
    func sub(_ from: Int, _ to: Int) -> Data { Data(self[(startIndex + from)..<(startIndex + to)]) }
}

func sha256Hex(_ d: Data) -> String {
    SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined()
}

/// A SHA-256 written as 64 hex digits, lower-cased; nil for anything else.
public func hex64(_ s: String?) -> String? {
    let t = (s ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    guard t.count == 64, t.allSatisfy({ $0.isHexDigit }) else { return nil }
    return t
}

/// One DER element: tag, content range, and where the element itself starts.
struct DER {
    let tag: UInt8
    let start: Int        // content start
    let end: Int          // content end
    let at: Int           // element start (tag byte)

    enum Bad: Error { case bad }

    static func read(_ b: Data, _ i0: Int) throws -> DER {
        var i = i0
        guard i + 2 <= b.count else { throw Bad.bad }
        let tag = UInt8(b.u8(i)); var n = b.u8(i + 1)
        i += 2
        if n & 0x80 != 0 {
            let k = n & 0x7F
            guard k > 0, k <= 4, i + k <= b.count else { throw Bad.bad }
            n = 0
            for j in 0..<k { n = n << 8 | b.u8(i + j) }
            i += k
        }
        guard i + n <= b.count else { throw Bad.bad }
        return DER(tag: tag, start: i, end: i + n, at: i0)
    }

    static func children(_ b: Data, _ start: Int, _ end: Int) throws -> [DER] {
        var out: [DER] = [], i = start
        while i < end {
            let e = try read(b, i)
            out.append(e)
            i = e.end
        }
        return out
    }

    func content(_ b: Data) -> Data { b.sub(start, end) }
    func element(_ b: Data) -> Data { b.sub(at, end) }
}

/// The certificates (DER) inside a PKCS#7 SignedData, in order.
func pkcs7Certificates(_ der: Data) throws -> [Data] {
    let ci = try DER.read(der, 0)
    let kids = try DER.children(der, ci.start, ci.end)
    guard kids.count >= 2, kids[1].tag == 0xA0 else { throw DER.Bad.bad }
    let sdEl = try DER.read(der, kids[1].start)
    let sd = try DER.children(der, sdEl.start, sdEl.end)
    guard let certs = sd.first(where: { $0.tag == 0xA0 }) else { return [] }
    return try DER.children(der, certs.start, certs.end).filter { $0.tag == 0x30 }.map { $0.element(der) }
}

// MARK: - ZIP (enough for APKs and F-Droid's entry.jar)

struct ZipReader {
    struct Entry { let name: String; let method: Int; let compSize: Int; let size: Int; let local: Int }
    let data: Data
    let entries: [Entry]

    init(_ data: Data) throws {
        let d = data.startIndex == 0 ? data : data.rebased
        self.data = d
        let tailLen = min(d.count, 0xFFFF + 22)
        let base = d.count - tailLen
        guard let e = d.sub(base, d.count).lastRange(of: Data([0x50, 0x4B, 0x05, 0x06])) else {
            throw CatalogError(CatalogText.notAPK)
        }
        let eocd = base + e.lowerBound
        guard eocd + 22 <= d.count else { throw CatalogError(CatalogText.notAPK) }
        let count = d.u16(eocd + 10)
        var p = d.u32(eocd + 16)
        var out: [Entry] = []
        for _ in 0..<count {
            guard p + 46 <= d.count, d.u32(p) == 0x02014B50 else { throw CatalogError(CatalogText.notAPK) }
            let nameLen = d.u16(p + 28), extra = d.u16(p + 30), comment = d.u16(p + 32)
            guard p + 46 + nameLen <= d.count else { throw CatalogError(CatalogText.notAPK) }
            let name = String(decoding: d.sub(p + 46, p + 46 + nameLen), as: UTF8.self)
            out.append(Entry(name: name, method: d.u16(p + 10), compSize: d.u32(p + 20),
                             size: d.u32(p + 24), local: d.u32(p + 42)))
            p += 46 + nameLen + extra + comment
        }
        entries = out
    }

    var names: [String] { entries.map(\.name) }

    func read(_ name: String) throws -> Data {
        guard let e = entries.first(where: { $0.name == name }) else { throw CatalogError(CatalogText.notAPK) }
        let l = e.local
        guard l + 30 <= data.count, data.u32(l) == 0x04034B50 else { throw CatalogError(CatalogText.notAPK) }
        let start = l + 30 + data.u16(l + 26) + data.u16(l + 28)
        guard start + e.compSize <= data.count else { throw CatalogError(CatalogText.notAPK) }
        let raw = data.sub(start, start + e.compSize)
        switch e.method {
        case 0: return raw
        case 8:
            if e.size == 0 { return Data() }
            guard e.size <= 64 << 20 else { throw CatalogError(CatalogText.notAPK) }
            var out = Data(count: e.size)
            let n = out.withUnsafeMutableBytes { dst in
                raw.withUnsafeBytes { src in
                    compression_decode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, e.size,
                                              src.bindMemory(to: UInt8.self).baseAddress!, raw.count,
                                              nil, COMPRESSION_ZLIB)
                }
            }
            guard n == e.size else { throw CatalogError(CatalogText.notAPK) }
            return out
        default:
            throw CatalogError(CatalogText.notAPK)
        }
    }
}

// MARK: - APK

public struct ApkInfo: Equatable, Sendable {
    public let pkg: String
    public let versionName: String
    public let versionCode: Int
    public let scheme: String          // v3.1 | v3 | v2 | v1
    public let signers: [String]       // SHA-256 of each signer certificate (hex)
}

public enum APKInspector {
    private static let schemes: [(UInt32, String)] = [(0x1B93AD61, "v3.1"), (0xF05368C0, "v3"), (0x7109871A, "v2")]

    private static func lp(_ b: Data, _ i: Int) throws -> (Data, Int) {
        guard i + 4 <= b.count else { throw DER.Bad.bad }
        let n = b.u32(i)
        guard i + 4 + n <= b.count else { throw DER.Bad.bad }
        return (b.sub(i + 4, i + 4 + n), i + 4 + n)
    }

    private static func lpItems(_ b: Data) throws -> [Data] {
        var out: [Data] = [], i = 0
        while i < b.count { let (item, next) = try lp(b, i); out.append(item); i = next }
        return out
    }

    /// Signer certificates from the APK Signing Block (v3.1 > v3 > v2); ("", []) if none.
    static func signingBlockCerts(_ d: Data) throws -> (String, [Data]) {
        let size = d.count
        let tailLen = min(size, 0xFFFF + 22)
        let tail = d.sub(size - tailLen, size)
        guard let r = tail.lastRange(of: Data([0x50, 0x4B, 0x05, 0x06])) else {
            throw CatalogError(CatalogText.notAPK)
        }
        let e = r.lowerBound
        guard e + 20 <= tail.count else { throw CatalogError(CatalogText.notAPK) }
        let cdOff = tail.u32(e + 16)
        if cdOff < 32 || cdOff > size { return ("", []) }
        let foot = d.sub(cdOff - 24, cdOff)
        guard foot.sub(8, 24) == Data("APK Sig Block 42".utf8) else { return ("", []) }
        let bsize64 = foot.u64(0)
        guard bsize64 <= UInt64(cdOff), bsize64 <= 64 << 20 else { return ("", []) }
        let bsize = Int(bsize64)
        guard cdOff - bsize - 8 >= 0, bsize >= 24 else { return ("", []) }
        let block = d.sub(cdOff - bsize - 8, cdOff)
        let pairs = block.sub(8, block.count - 24)
        var found: [UInt32: Data] = [:], i = 0
        while i + 12 <= pairs.count {
            let n = Int(pairs.u64(i))
            if n < 4 || i + 8 + n > pairs.count { break }
            found[UInt32(pairs.u32(i + 8))] = pairs.sub(i + 12, i + 8 + n)
            i += 8 + n
        }
        do {
            for (sid, name) in schemes {
                guard let value = found[sid] else { continue }
                var certs: [Data] = []
                for signer in try lpItems(try lp(value, 0).0) {
                    let (signedData, _) = try lp(signer, 0)
                    let (_, j) = try lp(signedData, 0)            // digests
                    let (certSeq, _) = try lp(signedData, j)
                    if let first = try lpItems(certSeq).first { certs.append(first) }   // the signer's own
                }
                if !certs.isEmpty { return (name, certs) }
            }
        } catch {
            throw CatalogError(CatalogText.notAPK)
        }
        return ("", [])
    }

    /// JAR signing (v1): the signer certificate of META-INF/*.RSA|DSA|EC.
    static func v1Certs(_ z: ZipReader) -> [Data] {
        let rx = try! NSRegularExpression(pattern: #"^META-INF/[^/]+\.(RSA|DSA|EC)$"#, options: [.caseInsensitive])
        var out: [Data] = []
        for n in z.names where rx.firstMatch(in: n, range: NSRange(n.startIndex..., in: n)) != nil {
            if let raw = try? z.read(n), let first = try? pkcs7Certificates(raw).first { out.append(first) }
        }
        return out
    }

    // --- AndroidManifest.xml is binary XML (AXML): <manifest package versionCode versionName>

    private static let attrIDs: [Int: String] = [0x0101021B: "versionCode", 0x0101021C: "versionName"]

    private static func axmlStrings(_ b: Data, _ at: Int) -> [String] {
        let hsz = b.u16(at + 2), count = b.u32(at + 8), flags = b.u32(at + 16), start = b.u32(at + 20)
        let utf8 = flags & 0x100 != 0
        let base = at + start
        var out: [String] = []
        for k in 0..<count {
            var p = base + b.u32(at + hsz + 4 * k)
            if utf8 {
                p += b.u8(p) & 0x80 != 0 ? 2 : 1                    // length in UTF-16 units (skipped)
                var n = b.u8(p)
                if n & 0x80 != 0 { n = (n & 0x7F) << 8 | b.u8(p + 1); p += 2 } else { p += 1 }
                out.append(String(decoding: b.sub(p, p + n), as: UTF8.self))
            } else {
                var n = b.u16(p); p += 2
                if n & 0x8000 != 0 { n = (n & 0x7FFF) << 16 | b.u16(p); p += 2 }
                out.append(String(data: b.sub(p, p + 2 * n), encoding: .utf16LittleEndian) ?? "")
            }
        }
        return out
    }

    /// package / versionCode / versionName of a binary AndroidManifest.xml.
    /// Values are String (string attributes) or Int (integer attributes).
    public static func parseManifest(_ buf: Data) throws -> [String: Any] {
        let b = buf.startIndex == 0 ? buf : buf.rebased
        guard b.count >= 8, b.u16(0) == 0x0003 else { throw CatalogError(CatalogText.notAPK) }
        var strings: [String] = [], resIDs: [Int] = [], i = 8
        while i + 8 <= b.count {
            let typ = b.u16(i), hsz = b.u16(i + 2), size = b.u32(i + 4)
            if size < 8 || i + size > b.count { break }
            if typ == 0x0001 {
                strings = axmlStrings(b, i)
            } else if typ == 0x0180 {
                resIDs = (0..<((size - hsz) / 4)).map { b.u32(i + hsz + 4 * $0) }
            } else if typ == 0x0102 {                            // the first element: <manifest>
                let nameI = b.u32(i + 20)
                let aStart = b.u16(i + 24), aSize = b.u16(i + 26), aCount = b.u16(i + 28)
                guard nameI < strings.count, strings[nameI] == "manifest" else { break }
                var out: [String: Any] = [:]
                for k in 0..<aCount {
                    let p = i + 16 + aStart + k * aSize
                    let an = b.u32(p + 4), raw = b.u32(p + 8), dtype = b.u8(p + 15), data = b.u32(p + 16)
                    var key: String? = an < strings.count && !strings[an].isEmpty ? strings[an] : nil
                    if key == nil, an < resIDs.count { key = attrIDs[resIDs[an]] }
                    guard let key, ["package", "versionCode", "versionName"].contains(key) else { continue }
                    if dtype == 0x03, raw != 0xFFFF_FFFF, raw < strings.count {
                        out[key] = strings[raw]
                    } else if dtype == 0x10 || dtype == 0x11 {
                        out[key] = data
                    }
                }
                return out
            }
            i += size
        }
        throw CatalogError(CatalogText.noManifest)
    }

    /// Package, version and signer certificates of an APK. The signature itself is
    /// checked by Android when it installs; here the certificate is compared with the
    /// one the developer is known to sign with.
    public static func info(_ data: Data) throws -> ApkInfo {
        let d = data.startIndex == 0 ? data : data.rebased
        var (scheme, certs) = try signingBlockCerts(d)
        let man: [String: Any]
        do {
            let z = try ZipReader(d)
            man = try parseManifest(try z.read("AndroidManifest.xml"))
            if certs.isEmpty { scheme = "v1"; certs = v1Certs(z) }
        } catch let e as CatalogError where e.message == CatalogText.noManifest {
            throw e
        } catch {
            throw CatalogError(CatalogText.notAPK)
        }
        if certs.isEmpty { throw CatalogError(CatalogText.unsigned) }
        let vc: Int
        if let n = man["versionCode"] as? Int { vc = n }
        else if let s = man["versionCode"] as? String, let n = Int(s) { vc = n }
        else { vc = 0 }
        return ApkInfo(pkg: (man["package"] as? String) ?? "",
                       versionName: (man["versionName"] as? String) ?? "",
                       versionCode: vc, scheme: scheme, signers: certs.map(sha256Hex))
    }

    public static func info(path: String) throws -> ApkInfo {
        guard let d = try? Data(contentsOf: URL(fileURLWithPath: path), options: .alwaysMapped) else {
            throw CatalogError(CatalogText.notAPK)
        }
        return try info(d)
    }
}
