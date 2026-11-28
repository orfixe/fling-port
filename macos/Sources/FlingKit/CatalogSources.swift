// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: where catalog APKs come from (desktop/fling_catalog.py). Each source says how
// far it is trusted:
//
//   GitHub      the project's own repository, latest non-prerelease release. The SHA-256 is
//               GitHub's `digest` of the uploaded asset and, where the project publishes one,
//               its checksum file — both must agree.
//   Mirrorbits  VideoLAN and Kodi send downloads to volunteer mirrors. The SHA-256 is asked
//               from the official redirector itself (`file?sha256`, no redirect allowed),
//               which hashes the master copy — a bad mirror cannot slip in another file.
//   Tailscale   pkgs.tailscale.com: the APK named by `?mode=json` and its `.sha256` file,
//               both from Tailscale's own server with redirects refused.
//   F-Droid     f-droid.org's signed index (CatalogFDroid.swift).
import CryptoKit
import Foundation

public struct Release: Codable, Equatable, Sendable {
    public var version: String
    public var url: String
    public var name: String
    public var size: Int = 0
    public var sha256: String?
    public var page: String = ""
    public var abi: String = ""                  // processor it is built for ("" = all)
    public var signers: [String] = []            // signer SHA-256s the F-Droid index lists

    public init(version: String, url: String, name: String, size: Int = 0, sha256: String? = nil,
                page: String = "", abi: String = "", signers: [String] = []) {
        self.version = version; self.url = url; self.name = name; self.size = size
        self.sha256 = sha256; self.page = page; self.abi = abi; self.signers = signers
    }
}

/// Network access the catalog needs. `strict`: from exactly this HTTPS host, redirects
/// refused (for checksums, which must not come from a mirror). Otherwise redirects are
/// followed only to HTTPS.
public protocol CatalogHTTP: Sendable {
    func get(_ url: String, limit: Int, strict: Bool) async throws -> Data
    /// Content-Length of a HEAD request (redirects refused), 0 when unknown.
    func headSize(_ url: String) async -> Int
    /// Download to `file`, at most `limit` bytes; progress(done, total).
    func download(_ url: String, to file: URL, limit: Int,
                  progress: @escaping @Sendable (Int, Int) -> Void) async throws
}

/// Display and install progress of one catalog job.
public enum CatalogStep: Equatable, Sendable {
    case meta, index(Int, Int), download(Int, Int), verify, sign, tv, tvCheck, tvRemove
}

public typealias CatalogProgress = @Sendable (CatalogStep) -> Void

public protocol CatalogSource: Sendable {
    var name: String { get }          // shown in the card: "GitHub", "VideoLAN"…
    var page: String { get }          // the official page it is shown with
    /// The newest stable release for the first of the TV's processors (in the TV's order)
    /// that the publisher builds for, with the publisher's SHA-256.
    func latest(abis: [String], http: CatalogHTTP, progress: CatalogProgress?) async throws -> Release
    /// The same for display; may skip the heavy parts.
    func peek(abis: [String], http: CatalogHTTP) async throws -> Release
}

public extension CatalogSource {
    func peek(abis: [String], http: CatalogHTTP) async throws -> Release {
        try await latest(abis: abis, http: http, progress: nil)
    }
}

/// The TV's first processor the publisher has a build for; "" = one APK for all.
func pickABI(_ abis: [String], _ have: Set<String>) -> String? {
    if have.contains("") { return "" }
    return abis.first(where: have.contains)
}

/// Whole-string regex match; returns the named group `v` (or the whole match) when it matches.
func fullMatch(_ pattern: String, _ s: String, group: String? = nil) -> String? {
    guard let rx = try? NSRegularExpression(pattern: "^(?:" + pattern + ")$"),
          let m = rx.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else { return nil }
    let r = group.map { m.range(withName: $0) } ?? m.range
    guard r.location != NSNotFound, let rr = Range(r, in: s) else { return "" }
    return String(s[rr])
}

func jsonObject(_ d: Data) throws -> [String: Any] {
    guard let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else {
        throw CatalogError(CatalogText.offline)
    }
    return o
}

/// The first SHA-256 listed for `name` in a checksum file ("<hex>  [*]name" lines).
func listedSHA(_ text: String, name: String) -> String? {
    for l in text.components(separatedBy: .newlines) {
        let parts = l.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
        if let last = parts.last, last.hasPrefix("*") ? String(last.dropFirst()) == name : last == name {
            return hex64(parts[0])
        }
    }
    return nil
}

/// Release assets of github.com/<repo>. assets: [abi or "": regex of the file name].
public struct GitHubSource: CatalogSource {
    public let name = "GitHub"
    public let repo: String
    public let assets: [String: String]
    public let sums: String
    public var page: String { "https://github.com/\(repo)/releases" }

    public init(_ repo: String, _ assets: [String: String], sums: String = "") {
        self.repo = repo; self.assets = assets; self.sums = sums
    }

    public func latest(abis: [String], http: CatalogHTTP, progress: CatalogProgress?) async throws -> Release {
        let rel = try jsonObject(try await http.get("https://api.github.com/repos/\(repo)/releases/latest",
                                                    limit: 8 << 20, strict: false))
        var byName: [String: [String: Any]] = [:]
        for case let a as [String: Any] in rel["assets"] as? [Any] ?? [] {
            byName[a["name"] as? String ?? ""] = a
        }
        var have: [String: String] = [:]
        for (abi, rx) in assets {
            if let n = byName.keys.sorted().first(where: { fullMatch(rx, $0) != nil }) { have[abi] = n }
        }
        guard let abi = pickABI(abis, Set(have.keys)), let fname = have[abi], let a = byName[fname] else {
            throw CatalogError(CatalogText.noBuild)
        }
        let digest = (a["digest"] as? String ?? "").lowercased()
        var sha = digest.hasPrefix("sha256:") ? hex64(String(digest.dropFirst(7))) : nil
        if !sums.isEmpty, let s = byName[sums], let u = s["browser_download_url"] as? String {
            let text = String(decoding: try await http.get(u, limit: 8 << 20, strict: false), as: UTF8.self)
            let listed = listedSHA(text, name: fname)
            if let sha, let listed, listed != sha { throw CatalogError(CatalogText.sumsDiffer) }
            sha = listed ?? sha
        }
        guard let sha else { throw CatalogError(CatalogText.noChecksum) }
        let url = a["browser_download_url"] as? String ?? ""
        guard url.hasPrefix("https://github.com/\(repo)/releases/download/") else {
            throw CatalogError(CatalogText.badLink)
        }
        var tag = rel["tag_name"] as? String ?? ""
        if tag.hasPrefix("v") { tag.removeFirst() }
        return Release(version: tag, url: url, name: fname, size: intValue(a["size"]), sha256: sha,
                       page: rel["html_url"] as? String ?? page, abi: abi)
    }
}

/// An official download host that redirects files to mirrors (mirrorbits): VideoLAN,
/// Kodi. dirs: [abi: official folder URL]; rx: file name with (?<v>version) and {abi}.
public struct MirrorbitsSource: CatalogSource {
    public let name: String
    public let page: String
    public let dirs: [String: String]
    public let rx: String

    public init(_ name: String, _ page: String, _ dirs: [String: String], _ rx: String) {
        self.name = name; self.page = page; self.dirs = dirs; self.rx = rx
    }

    public func latest(abis: [String], http: CatalogHTTP, progress: CatalogProgress?) async throws -> Release {
        guard let abi = pickABI(abis, Set(dirs.keys)), let folder = dirs[abi] else {
            throw CatalogError(CatalogText.noBuild)
        }
        let html = String(decoding: try await http.get(folder, limit: 8 << 20, strict: false), as: UTF8.self)
        let pattern = rx.replacingOccurrences(of: "{abi}", with: NSRegularExpression.escapedPattern(for: abi))
        let href = try! NSRegularExpression(pattern: #"href="([^"/?#]+)""#)
        var files: [([Int], String, String)] = []
        for m in href.matches(in: html, range: NSRange(html.startIndex..., in: html)) {
            guard let r = Range(m.range(at: 1), in: html) else { continue }
            let f = String(html[r])
            if let v = fullMatch(pattern, f, group: "v") { files.append((ToolVersion.parse(v), v, f)) }
        }
        guard let best = files.max(by: { ToolVersion.less($0.0, $1.0) || ($0.0 == $1.0 && $0.2 < $1.2) }) else {
            throw CatalogError(CatalogText.noBuild)
        }
        let (ver, fname) = (best.1, best.2)
        let url = folder + fname
        // the checksum from the official host itself, never from a mirror
        let line = String(decoding: try await http.get(url + "?sha256", limit: 4096, strict: true), as: UTF8.self)
            .split(whereSeparator: \.isWhitespace).map(String.init)
        let sha = line.first.flatMap(hex64)
        var nameOK = true
        if line.count > 1, let last = line.last {
            nameOK = (last.hasPrefix("*") ? String(last.dropFirst()) : last) == fname
        }
        guard let sha, nameOK else { throw CatalogError(CatalogText.noChecksum) }
        var size = 0
        if let info = try? await http.get(url + "?mirrorlist", limit: 512 << 10, strict: true) {
            let t = String(decoding: info, as: UTF8.self)
            if let m = try! NSRegularExpression(pattern: #"\((\d+) bytes\)"#)
                .firstMatch(in: t, range: NSRange(t.startIndex..., in: t)),
               let r = Range(m.range(at: 1), in: t) { size = Int(t[r]) ?? 0 }
        }
        return Release(version: ver, url: url, name: fname, size: size, sha256: sha, page: page, abi: abi)
    }
}

/// pkgs.tailscale.com, Tailscale's own package server. The APK is signed with Tailscale's
/// own key — the same one as in Google Play, so it also updates a Play Store copy.
public struct TailscaleSource: CatalogSource {
    public let name = "Tailscale"
    public let page = "https://pkgs.tailscale.com/stable/#android"
    static let base = "https://pkgs.tailscale.com/stable/"

    public init() {}

    public func latest(abis: [String], http: CatalogHTTP, progress: CatalogProgress?) async throws -> Release {
        let d = try jsonObject(try await http.get(Self.base + "?mode=json", limit: 256 << 10, strict: true))
        let fname = (d["APKs"] as? [String: Any])?["universal"] as? String ?? ""
        let ver = d["APKsVersion"] as? String ?? ""
        guard fullMatch(#"tailscale-android-universal-[\d.]+\.apk"#, fname) != nil else {
            throw CatalogError(CatalogText.noBuild)
        }
        let url = Self.base + fname
        let line = String(decoding: try await http.get(url + ".sha256", limit: 4096, strict: true), as: UTF8.self)
            .split(whereSeparator: \.isWhitespace).map(String.init)
        guard let sha = line.first.flatMap(hex64) else { throw CatalogError(CatalogText.noChecksum) }
        let size = await http.headSize(url)
        return Release(version: ver, url: url, name: fname, size: size, sha256: sha, page: page, abi: "")
    }
}

/// Keeps the verified F-Droid index between installs and remembers the newest entry
/// timestamp seen (no rollback to an older catalogue).
public protocol FDroidIndexStore: Sendable {
    var seenTimestamp: Int { get }
    func remember(timestamp: Int)
    /// A local index file already verified with this SHA-256 and size, if there is one.
    func cachedIndex(sha256: String, size: Int) -> URL?
    /// Where to download a new index (then `accept` it once verified).
    func partURL() -> URL
    func accept(part: URL, sha256: String) throws -> URL
}

/// An app from f-droid.org's main repository, checked through the signed index.
public struct FDroidSource: CatalogSource {
    public let name = "F-Droid"
    public let pkg: String
    public let store: FDroidIndexStore
    public var page: String { "https://f-droid.org/packages/\(pkg)/" }

    public init(_ pkg: String, store: FDroidIndexStore = DiskFDroidIndexStore()) {
        self.pkg = pkg; self.store = store
    }

    /// The verified index-v2.json (downloaded again only when F-Droid publishes a new one).
    func index(http: CatalogHTTP, progress: CatalogProgress?) async throws -> URL {
        let jar = try await http.get(FDroidIndex.repo + "/entry.jar", limit: 1 << 20, strict: false)
        let entry = try FDroidIndex.verifyEntryJar(jar)
        let ref = try FDroidIndex.indexRef(entry: entry, seenTimestamp: store.seenTimestamp)
        if let have = store.cachedIndex(sha256: ref.sha256, size: ref.size) {
            store.remember(timestamp: ref.timestamp)
            return have
        }
        let part = store.partURL()
        try? FileManager.default.removeItem(at: part)
        progress?(.index(0, ref.size))
        do {
            try await http.download(FDroidIndex.repo + "/index-v2.json", to: part, limit: ref.size) { done, _ in
                progress?(.index(done, ref.size))
            }
        } catch let e as CatalogError where e.message == CatalogText.archiveTooBig {
            try? FileManager.default.removeItem(at: part)
            throw CatalogError(CatalogText.fdroidDamaged)
        }
        guard fileSize(part) == ref.size, (try? fileSHA256(part)) == ref.sha256 else {
            try? FileManager.default.removeItem(at: part)
            throw CatalogError(CatalogText.fdroidDamaged)
        }
        let path = try store.accept(part: part, sha256: ref.sha256)
        store.remember(timestamp: ref.timestamp)
        return path
    }

    public func latest(abis: [String], http: CatalogHTTP, progress: CatalogProgress?) async throws -> Release {
        let file = try await index(http: http, progress: progress)
        guard let data = try? Data(contentsOf: file, options: .alwaysMapped) else {
            throw CatalogError(CatalogText.fdroidMissing)
        }
        return try Self.release(index: data, pkg: pkg, abis: abis, page: page)
    }

    /// The release the verified index lists for `pkg` (split out for tests).
    static func release(index: Data, pkg: String, abis: [String], page: String) throws -> Release {
        let (v, abi) = try FDroidIndex.pick(try FDroidIndex.package(indexData: index, pkg: pkg), abis: abis)
        let man = v["manifest"] as? [String: Any] ?? [:]
        let file = v["file"] as? [String: Any] ?? [:]
        let fname = file["name"] as? String ?? ""
        guard fullMatch(#"/[A-Za-z0-9._-]+\.apk"#, fname) != nil, let sha = hex64(file["sha256"] as? String) else {
            throw CatalogError(CatalogText.noChecksum)
        }
        let signers = ((man["signer"] as? [String: Any])?["sha256"] as? [String] ?? []).compactMap(hex64)
        return Release(version: man["versionName"] as? String ?? "", url: FDroidIndex.repo + fname,
                       name: String(fname.dropFirst()), size: intValue(file["size"]), sha256: sha,
                       page: page, abi: abi, signers: signers)
    }

    /// For display: F-Droid's public API names the suggested version. Nothing is
    /// installed from this — install uses latest().
    public func peek(abis: [String], http: CatalogHTTP) async throws -> Release {
        let d = try jsonObject(try await http.get("https://f-droid.org/api/v1/packages/\(pkg)",
                                                  limit: 1 << 20, strict: false))
        let code = intValue(d["suggestedVersionCode"])
        let pkgs = (d["packages"] as? [Any] ?? []).compactMap { $0 as? [String: Any] }
        let v = pkgs.first(where: { intValue($0["versionCode"]) == code }) ?? pkgs.first ?? [:]
        let fname = "\(pkg)_\(intValue(v["versionCode"])).apk"
        let size = await http.headSize("\(FDroidIndex.repo)/\(fname)")
        return Release(version: v["versionName"] as? String ?? "", url: "\(FDroidIndex.repo)/\(fname)",
                       name: fname, size: size, page: page)
    }
}

/// "1.104.7-tfb3b9b296-g226f029a3" → "1.104.7" for display.
public func shortVersion(_ v: String) -> String {
    if let r = v.range(of: #"^\d+(?:\.\d+)+"#, options: .regularExpression) { return String(v[r]) }
    return v
}

func fileSize(_ url: URL) -> Int {
    ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.intValue ?? -1
}

func fileSHA256(_ url: URL) throws -> String {
    let h = try FileHandle(forReadingFrom: url)
    defer { try? h.close() }
    var hasher = SHA256Stream()
    while let chunk = try h.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(chunk) }
    return hasher.hex()
}

struct SHA256Stream {
    private var h = SHA256()
    mutating func update(_ d: Data) { h.update(data: d) }
    func hex() -> String { h.finalize().map { String(format: "%02x", $0) }.joined() }
}

/// The F-Droid index and its bookkeeping in ~/Library/Caches/Fling Port.
public final class DiskFDroidIndexStore: FDroidIndexStore, @unchecked Sendable {
    private let dir: URL
    private let defaults: UserDefaults
    private static let key = "catalog.fdroidTimestamp"

    public init(dir: URL? = nil, defaults: UserDefaults = .standard) {
        self.dir = dir ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Fling Port", isDirectory: true)
        self.defaults = defaults
    }

    private var index: URL { dir.appendingPathComponent("fdroid-index-v2.json") }
    public var seenTimestamp: Int { defaults.integer(forKey: Self.key) }

    public func remember(timestamp: Int) {
        defaults.set(max(timestamp, seenTimestamp), forKey: Self.key)
    }

    public func cachedIndex(sha256: String, size: Int) -> URL? {
        let mark = (try? String(contentsOf: index.appendingPathExtension("sha256"), encoding: .ascii))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return mark == sha256 && fileSize(index) == size ? index : nil
    }

    public func partURL() -> URL {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return index.appendingPathExtension("part")
    }

    public func accept(part: URL, sha256: String) throws -> URL {
        try? FileManager.default.removeItem(at: index)
        try FileManager.default.moveItem(at: part, to: index)
        try sha256.write(to: index.appendingPathExtension("sha256"), atomically: true, encoding: .ascii)
        return index
    }
}
