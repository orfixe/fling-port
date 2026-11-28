// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: F-Droid's signed repository index (desktop/fling_catalog.py). entry.jar is
// signed with the F-Droid repository key (pinned below); it gives the SHA-256 of
// index-v2.json, and that index gives the SHA-256, size and signer of every APK.
import CryptoKit
import Foundation
import Security

public enum FDroidIndex {
    public static let repo = "https://f-droid.org/repo"
    /// SHA-256 of the F-Droid repository signing certificate (f-droid.org's published fingerprint).
    public static let repoCert = "43238d512c1e5eb2d6569f4a3afbf5523418b82e0a3ed1552770abb9a9c9ccab"
    public static let indexMax = 256 << 20

    private static let oidSHA256 = Data([0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01])
    private static let oidMessageDigest = Data([0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x09, 0x04])
    private static let oidRSA = Data([0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01])
    private static let oidSHA256RSA = Data([0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x0b])

    private static func oid(in b: Data, _ el: DER) throws -> Data {
        let kids = try DER.children(b, el.start, el.end)
        guard let first = kids.first, first.tag == 0x06 else { return Data() }
        return first.content(b)
    }

    /// A detached PKCS#7 SignedData over `data` (a JAR's .SF), made by the pinned RSA
    /// certificate with SHA-256. Throws CatalogError on anything else.
    public static func verifyPKCS7(_ sigDER: Data, data: Data, pinnedCertSHA256: String) throws {
        let bad = CatalogError(CatalogText.fdroidSignature)
        let sd = sigDER.startIndex == 0 ? sigDER : sigDER.rebased
        do {
            guard let certDER = try pkcs7Certificates(sd).first(where: { sha256Hex($0) == pinnedCertSHA256 }),
                  let cert = SecCertificateCreateWithData(nil, certDER as CFData),
                  let key = SecCertificateCopyKey(cert),
                  let attrs = SecKeyCopyAttributes(key) as? [CFString: Any],
                  (attrs[kSecAttrKeyType] as? String) == (kSecAttrKeyTypeRSA as String)
            else { throw bad }
            let ci = try DER.read(sd, 0)
            let ciKids = try DER.children(sd, ci.start, ci.end)
            guard ciKids.count >= 2 else { throw bad }
            let sdEl = try DER.read(sd, ciKids[1].start)                    // [0] → SignedData
            let sdKids = try DER.children(sd, sdEl.start, sdEl.end)
            guard let infos = sdKids.last, infos.tag == 0x31 else { throw bad }
            let signer = try DER.children(sd, infos.start, infos.end)
            guard signer.count == 1 else { throw bad }
            let si = try DER.children(sd, signer[0].start, signer[0].end)
            guard si.count >= 5, try oid(in: sd, si[2]) == oidSHA256 else { throw bad }
            var k = 3
            var signed = data
            if si[3].tag == 0xA0 {                                          // signed attributes
                let attrs = si[3]
                var md: Data?
                for a in try DER.children(sd, attrs.start, attrs.end) {
                    let ak = try DER.children(sd, a.start, a.end)
                    guard ak.count >= 2 else { continue }
                    if ak[0].content(sd) == oidMessageDigest {
                        md = try DER.children(sd, ak[1].start, ak[1].end).first?.content(sd)
                    }
                }
                guard md == Data(SHA256.hash(data: data)) else { throw bad }
                signed = Data([0x31]) + sd.sub(attrs.at + 1, attrs.end)
                k = 4
            }
            guard si.count > k + 1 else { throw bad }
            let alg = try oid(in: sd, si[k])
            guard alg == oidRSA || alg == oidSHA256RSA, si[k + 1].tag == 0x04 else { throw bad }
            let sig = si[k + 1].content(sd)
            var err: Unmanaged<CFError>?
            guard SecKeyVerifySignature(key, .rsaSignatureMessagePKCS1v15SHA256,
                                        signed as CFData, sig as CFData, &err) else { throw bad }
        } catch let e as CatalogError {
            throw e
        } catch {
            throw bad
        }
    }

    /// A JAR manifest / .SF: continuation lines joined, one dictionary per section.
    public static func manifestSections(_ text: String) -> [[String: String]] {
        var lines: [String] = []
        for raw in text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            if raw.hasPrefix(" "), !lines.isEmpty { lines[lines.count - 1] += String(raw.dropFirst()) }
            else { lines.append(raw) }
        }
        var out: [[String: String]] = [], cur: [String: String] = [:]
        for l in lines {
            if l.isEmpty {
                if !cur.isEmpty { out.append(cur) }
                cur = [:]
                continue
            }
            if let r = l.range(of: ": ") { cur[String(l[..<r.lowerBound])] = String(l[r.upperBound...]) }
            else { cur[l] = "" }
        }
        if !cur.isEmpty { out.append(cur) }
        return out
    }

    /// F-Droid's entry.jar → entry.json, only if the JAR is signed by the pinned key.
    public static func verifyEntryJar(_ data: Data, pinned: String = repoCert) throws -> [String: Any] {
        let bad = CatalogError(CatalogText.fdroidSignature)
        guard let z = try? ZipReader(data) else { throw bad }
        let names = z.names
        let sfs = names.filter { $0.hasPrefix("META-INF/") && $0.hasSuffix(".SF") && !$0.dropFirst(9).contains("/") }
        guard sfs.count == 1 else { throw bad }
        let sfName = sfs[0], rsaName = String(sfName.dropLast(3)) + ".RSA"
        let allowed: Set<String> = ["entry.json", "META-INF/MANIFEST.MF", sfName, rsaName]
        guard Set(names).subtracting(allowed).isEmpty,
              let entry = try? z.read("entry.json"), let mf = try? z.read("META-INF/MANIFEST.MF"),
              let sf = try? z.read(sfName), let rsa = try? z.read(rsaName) else { throw bad }
        try verifyPKCS7(rsa, data: sf, pinnedCertSHA256: pinned)
        let b64 = { (d: Data) in Data(SHA256.hash(data: d)).base64EncodedString() }
        guard manifestSections(String(decoding: sf, as: UTF8.self)).first?["SHA-256-Digest-Manifest"] == b64(mf)
        else { throw bad }
        let sec = manifestSections(String(decoding: mf, as: UTF8.self)).first { $0["Name"] == "entry.json" } ?? [:]
        guard sec["SHA-256-Digest"] == b64(entry),
              let obj = try? JSONSerialization.jsonObject(with: entry) as? [String: Any] else { throw bad }
        return obj
    }

    /// What entry.json says about index-v2.json: (sha256, size, timestamp). Old entries
    /// (timestamp below one already seen) are refused, so a stale catalogue can't come back.
    public static func indexRef(entry: [String: Any], seenTimestamp: Int) throws -> (sha256: String, size: Int, timestamp: Int) {
        let ts = intValue(entry["timestamp"])
        if ts < seenTimestamp { throw CatalogError(CatalogText.fdroidOld) }
        let idx = entry["index"] as? [String: Any] ?? [:]
        let size = intValue(idx["size"])
        guard let want = hex64(idx["sha256"] as? String), idx["name"] as? String == "/index-v2.json",
              size > 0, size <= indexMax else { throw CatalogError(CatalogText.fdroidSignature) }
        return (want, size, ts)
    }

    /// One package of the (verified) index, without parsing the whole ~75 MB of JSON.
    public static func package(indexData mm: Data, pkg: String) throws -> [String: Any] {
        let missing = CatalogError(CatalogText.fdroidMissing)
        let d = mm.startIndex == 0 ? mm : mm.rebased
        guard let p0 = d.range(of: Data(#""packages":"#.utf8)) else { throw missing }
        let quoted = (try? JSONSerialization.data(withJSONObject: [pkg]))
            .map { String(decoding: $0, as: UTF8.self).dropFirst().dropLast() }.map(String.init) ?? "\"\(pkg)\""
        var start: Int?
        for key in [quoted + ": {", quoted + ":{"] {
            if let r = d.range(of: Data(key.utf8), in: p0.upperBound..<d.count) {
                start = r.upperBound - 1
                break
            }
        }
        guard let s = start, let end = matchingBrace(d, s, limit: 64 << 20),
              let obj = try? JSONSerialization.jsonObject(with: d.sub(s, end + 1)) as? [String: Any],
              obj["versions"] is [String: Any] else { throw missing }
        return obj
    }

    /// Index of the `}` that closes the object starting at `start`, skipping strings.
    static func matchingBrace(_ d: Data, _ start: Int, limit: Int) -> Int? {
        var depth = 0, inString = false, escape = false
        let stop = min(d.count, start + limit)
        return d.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> Int? in
            let b = raw.bindMemory(to: UInt8.self)
            var i = start
            while i < stop {
                let c = b[i]
                if inString {
                    if escape { escape = false }
                    else if c == 0x5C { escape = true }
                    else if c == 0x22 { inString = false }
                } else if c == 0x22 { inString = true }
                else if c == 0x7B { depth += 1 }
                else if c == 0x7D { depth -= 1; if depth == 0 { return i } }
                i += 1
            }
            return nil
        }
    }

    /// The newest stable version that runs on one of the TV's processors → (version entry, abi).
    public static func pick(_ pkgEntry: [String: Any], abis: [String]) throws -> ([String: Any], String) {
        var best: (Int, [String: Any], String)?
        for case let v as [String: Any] in (pkgEntry["versions"] as? [String: Any] ?? [:]).values {
            let man = v["manifest"] as? [String: Any] ?? [:]
            if let ch = v["releaseChannels"] as? [Any], !ch.isEmpty { continue }   // beta channel
            let native = man["nativecode"] as? [String] ?? []
            let abi: String
            if native.isEmpty { abi = "" }
            else if let a = abis.first(where: native.contains) { abi = a }
            else { continue }
            let code = intValue(man["versionCode"])
            if best == nil || code > best!.0 { best = (code, v, abi) }
        }
        guard let best else { throw CatalogError(CatalogText.noBuild) }
        return (best.1, best.2)
    }
}

func intValue(_ v: Any?) -> Int {
    if let n = v as? Int { return n }
    if let n = v as? NSNumber { return n.intValue }
    if let s = v as? String, let n = Int(s) { return n }
    return 0
}
