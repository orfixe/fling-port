// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: the Catalog's install / remove on the TV (desktop/fling_catalog.py). Download
// from the official source, check the publisher's SHA-256, check that the APK is the app
// we mean and is signed by the developer's own key, then `adb install`. A download that
// fails any check is deleted and never reaches the TV.
//
// ADB itself goes through `CatalogADB`, so the port's ADB module can be plugged in
// (`CatalogADBProvider.make`); `ProcessCatalogADB` is a plain stand-in.
import Foundation

/// The TV over ADB, as the catalog needs it.
public protocol CatalogADB: Sendable {
    /// nil when an authorised ADB link to the TV is ready; otherwise what to tell the user.
    /// `connect`: try to connect first (a click on Connect ADB / Install); without it only
    /// an existing link counts (opening the window must not reach out to the TV).
    func ready(ip: String, connect: Bool) async -> String?
    /// `adb -s <tv> <args…>`.
    func run(ip: String, args: [String], timeout: TimeInterval) async -> ProcessResult
}

public enum CatalogADBProvider {
    /// Swap for the ADB module's implementation at merge time.
    nonisolated(unsafe) public static var make: () -> CatalogADB = { ADBClientCatalogADB() }   // port: shared with the ADB panel (CatalogADBBridge.swift)
}

/// Stand-in: the adb ToolLocator finds, the TV on the default port 5555.
public struct ProcessCatalogADB: CatalogADB {
    public init() {}

    static func serial(_ ip: String) -> String { ip.contains(":") ? ip : "\(ip):5555" }

    public func ready(ip: String, connect: Bool) async -> String? {
        guard let adb = ToolLocator.find("adb") else { return CatalogInstaller.adbMissing }
        return await Task.detached {
            let r = SystemProcessRunner()
            if connect { _ = try? r.run(adb, ["connect", Self.serial(ip)], timeout: 15) }
            let st = try? r.run(adb, ["-s", Self.serial(ip), "get-state"], timeout: 10)
            if st?.stdout.trimmingCharacters(in: .whitespacesAndNewlines) == "device" { return nil }
            if (st?.stderr ?? "").contains("unauthorized") { return CatalogInstaller.adbUnauthorized }
            return CatalogInstaller.adbNotConnected
        }.value
    }

    public func run(ip: String, args: [String], timeout: TimeInterval) async -> ProcessResult {
        guard let adb = ToolLocator.find("adb") else {
            return ProcessResult(exitCode: -1, stdout: "", stderr: CatalogInstaller.adbMissing)
        }
        return await Task.detached {
            (try? SystemProcessRunner().run(adb, ["-s", Self.serial(ip)] + args, timeout: timeout))
                ?? ProcessResult(exitCode: -1, stdout: "", stderr: "adb did not start")
        }.value
    }
}

public struct TvInfo: Equatable, Sendable {
    public var ip = ""
    public var name = ""
    public var adb = false                     // an authorised ADB link right now
    public var abis: [String] = []
    public var android = ""
    public var installed: [String: String] = [:]   // pkg → versionName ("" if unknown)
    public var error = ""
    public init(ip: String = "", name: String = "", adb: Bool = false) {
        self.ip = ip; self.name = name; self.adb = adb
    }
}

public struct CatalogInstaller: Sendable {
    public static let maxDownload = 300 << 20
    public static let peekTTL: TimeInterval = 6 * 3600

    public static let adbMissing = "adb isn't installed — install it in the Tools tab."
    public static let adbNotConnected = "ADB isn't connected. Turn on network debugging on the TV and connect ADB first."
    public static let adbUnauthorized = "The TV hasn't allowed this Mac yet — confirm the ADB prompt on the TV screen."
    public static let installedMessage = "Installed on the TV: {name} {version}. The developer's signature checked out."
    public static let removedMessage = "Removed from the TV: {name}."

    static let apkErrors: [String: String] = [
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "the TV already has this app with a different signature (from Google Play, for example) — remove it on the TV and try again.",
        "INSTALL_FAILED_VERSION_DOWNGRADE": "the TV already has a newer version.",
        "INSTALL_FAILED_NO_MATCHING_ABIS": "this build doesn't fit the TV's processor.",
        "INSTALL_FAILED_OLDER_SDK": "the app needs a newer Android than the TV has.",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "the TV is out of space.",
        "INSTALL_FAILED_USER_RESTRICTED": "the TV blocked the install — allow it on the TV screen.",
        "INSTALL_FAILED_VERIFICATION_FAILURE": "the TV rejected the app (Google Play Protect check).",
    ]

    public let http: CatalogHTTP
    public let adb: CatalogADB
    public let workDir: URL

    public init(http: CatalogHTTP = URLSessionCatalogHTTP(), adb: CatalogADB = CatalogADBProvider.make(),
                workDir: URL? = nil) {
        self.http = http; self.adb = adb
        self.workDir = workDir ?? FileManager.default.temporaryDirectory.appendingPathComponent("fling-catalog", isDirectory: true)
    }

    static func validPackage(_ p: String) -> Bool {
        fullMatch(#"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+"#, p) != nil
    }

    /// Processor, Android version and installed catalog apps — read-only commands over an
    /// ADB link that is already authorised.
    public func probe(_ base: TvInfo) async -> TvInfo {
        var info = base
        guard !info.ip.isEmpty, info.adb else { return info }
        let r = await adb.run(ip: info.ip, args: ["shell", "getprop ro.product.cpu.abilist; echo @; getprop ro.product.cpu.abi; echo @; getprop ro.build.version.release; echo @; pm list packages"], timeout: 20)
        guard r.exitCode == 0 else { info.error = "the TV didn't answer over ADB"; return info }
        Self.parseProbe(r.stdout, into: &info)
        let mine = Catalog.apps.map(\.pkg).filter { info.installed[$0] != nil && Self.validPackage($0) }
        if !mine.isEmpty {
            let script = mine.map { "echo @\($0); dumpsys package \($0) | grep -m1 versionName=" }.joined(separator: "; ")
            let v = await adb.run(ip: info.ip, args: ["shell", script], timeout: 20)
            Self.parseVersions(v.stdout, into: &info)
        }
        return info
    }

    static func parseProbe(_ out: String, into info: inout TvInfo) {
        var parts = out.replacingOccurrences(of: "\r", with: "").components(separatedBy: "@\n")
        while parts.count < 4 { parts.append("") }
        let p0 = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
        let p1 = parts[1].trimmingCharacters(in: .whitespacesAndNewlines)
        var abis = p0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        if abis.isEmpty, !p1.isEmpty { abis = [p1] }
        info.abis = abis.filter(Catalog.abis.contains)
        info.android = parts[2].trimmingCharacters(in: .whitespacesAndNewlines)
        let pkgs = Set(parts[3].components(separatedBy: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { $0.hasPrefix("package:") }.map { String($0.dropFirst(8)) })
        info.installed = [:]
        for a in Catalog.apps where pkgs.contains(a.pkg) { info.installed[a.pkg] = "" }
    }

    static func parseVersions(_ out: String, into info: inout TvInfo) {
        var cur: String?
        for raw in out.components(separatedBy: "\n") {
            let l = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if l.hasPrefix("@") { cur = String(l.dropFirst()) }
            else if let c = cur, let r = l.range(of: "versionName=") {
                info.installed[c] = String(l[r.upperBound...]).split(separator: " ").first.map(String.init) ?? ""
            }
        }
    }

    // MARK: - checks

    static func pins(_ app: CatalogApp, _ rel: Release) -> Set<String> {
        var pins = Set(app.signers.compactMap(hex64))
        if app.source is FDroidSource { pins.formUnion(rel.signers.compactMap(hex64)) }   // the signed index names the signer
        return pins
    }

    /// The downloaded APK is the app we mean, signed by the developer's key.
    public static func checkAPK(_ app: CatalogApp, data: Data, release: Release) throws -> ApkInfo {
        let info = try APKInspector.info(data)
        guard info.pkg == app.pkg else { throw CatalogError(CatalogText.otherApp) }
        let p = pins(app, release)
        guard !p.isEmpty else { throw CatalogError(CatalogText.unknownKey) }
        guard !Set(info.signers).isDisjoint(with: p) else { throw CatalogError(CatalogText.wrongKey) }
        return info
    }

    /// The publisher's SHA-256 (and size, when known) of a downloaded file.
    public static func checkDownload(_ file: URL, release: Release) throws {
        guard let want = hex64(release.sha256) else { throw CatalogError(CatalogText.noChecksum) }
        let size = fileSize(file)
        if release.size > 0, size != release.size { throw CatalogError(CatalogText.truncated) }
        guard (try? fileSHA256(file)) == want else { throw CatalogError(CatalogText.damaged) }
    }

    static func installError(_ out: String) -> String {
        if let r = out.range(of: #"INSTALL_[A-Z_]+"#, options: .regularExpression) {
            let code = String(out[r])
            return "the TV didn't install the app: " + (apkErrors[code] ?? code)
        }
        let last = out.components(separatedBy: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
            .last(where: { !$0.isEmpty }) ?? "no answer"
        return "the TV didn't install the app: " + last
    }

    // MARK: - install / remove

    /// Download, verify and install `app` on the TV at `ip`. Returns the installed version.
    public func install(_ app: CatalogApp, ip: String, progress: @escaping CatalogProgress) async throws -> String {
        guard !ip.isEmpty else { throw CatalogError("no TV is chosen") }
        progress(.meta)
        if let err = await adb.ready(ip: ip, connect: true) { throw CatalogError(err) }
        let info = await probe(TvInfo(ip: ip, name: ip, adb: true))
        var rel = try await app.source.latest(abis: info.abis.isEmpty ? Catalog.defaultABIs : info.abis,
                                              http: http, progress: progress)
        guard hex64(rel.sha256) != nil else { throw CatalogError(CatalogText.noChecksum) }
        if rel.size > Self.maxDownload { throw CatalogError(CatalogText.archiveTooBig) }
        guard rel.url.hasPrefix("https://") else { throw CatalogError(CatalogText.httpsOnly) }
        rel.name = "\(app.key)-\((rel.name as NSString).lastPathComponent)"
        let dir = workDir.appendingPathComponent("\(app.key)-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let apk = dir.appendingPathComponent(app.pkg + ".apk")        // adb wants the .apk ending
        let expected = rel.size
        try await http.download(rel.url, to: apk, limit: Self.maxDownload) { done, total in
            progress(.download(done, total > 0 ? total : expected))
        }
        progress(.verify)
        try Self.checkDownload(apk, release: rel)                     // checksum verified here
        progress(.sign)
        guard let data = try? Data(contentsOf: apk, options: .alwaysMapped) else { throw CatalogError(CatalogText.notAPK) }
        let checked = try Self.checkAPK(app, data: data, release: rel)
        progress(.tv)
        let r = await adb.run(ip: ip, args: ["install", "-r", apk.path], timeout: 900)
        let out = r.stdout + "\n" + r.stderr
        guard r.exitCode == 0, out.contains("Success") else { throw CatalogError(Self.installError(out)) }
        progress(.tvCheck)
        let chk = await adb.run(ip: ip, args: ["shell", "pm list packages \(app.pkg)"], timeout: 20)
        guard chk.stdout.split(whereSeparator: \.isWhitespace).contains(where: { $0 == "package:\(app.pkg)" }) else {
            throw CatalogError("the TV reported success, but the app isn't there")
        }
        NSLog("catalog: %@ %@ signer %@ (%@) sha256 %@ from %@", app.pkg, checked.versionName,
              checked.signers.first ?? "", checked.scheme, rel.sha256 ?? "", rel.url)
        let v = shortVersion(checked.versionName)
        return v.isEmpty ? rel.version : v
    }

    public func remove(_ app: CatalogApp, ip: String, progress: @escaping CatalogProgress) async throws {
        guard !ip.isEmpty, Self.validPackage(app.pkg) else { throw CatalogError("no TV is chosen") }
        progress(.tvRemove)
        if let err = await adb.ready(ip: ip, connect: true) { throw CatalogError(err) }
        let r = await adb.run(ip: ip, args: ["uninstall", app.pkg], timeout: 120)
        guard r.exitCode == 0, (r.stdout + "\n" + r.stderr).contains("Success") else {
            throw CatalogError("the TV didn't remove the app")
        }
    }

    /// Opens the app on the TV through its launcher (Android TV / Google TV).
    public func open(_ app: CatalogApp, ip: String) async {
        guard Self.validPackage(app.pkg) else { return }
        _ = await adb.run(ip: ip, args: ["shell", "monkey -p \(app.pkg) -c android.intent.category.LEANBACK_LAUNCHER 1 || monkey -p \(app.pkg) -c android.intent.category.LAUNCHER 1"], timeout: 20)
    }
}

/// The latest version for display, remembered for a while so the publishers aren't asked
/// on every opening (GitHub limits anonymous requests); a stale answer beats an error.
public actor CatalogPeekCache {
    private var entries: [String: (Date, Release)] = [:]
    private let http: CatalogHTTP

    public init(http: CatalogHTTP = URLSessionCatalogHTTP()) { self.http = http }

    public func peek(_ app: CatalogApp, abis: [String], fresh: Bool = false) async throws -> Release {
        let key = "\(app.key)|\(abis.joined(separator: ","))"
        let old = entries[key]
        if let old, Date().timeIntervalSince(old.0) < (fresh ? 600 : CatalogInstaller.peekTTL) { return old.1 }
        do {
            let rel = try await app.source.peek(abis: abis, http: http)
            entries[key] = (Date(), rel)
            return rel
        } catch {
            if let old { return old.1 }
            throw error
        }
    }
}

// MARK: - HTTPS

/// URLSession with the catalog's rules: HTTPS only, redirects only to HTTPS, none at all
/// for `strict` requests, size limits.
public final class URLSessionCatalogHTTP: NSObject, CatalogHTTP, URLSessionDataDelegate, @unchecked Sendable {
    public static let userAgent = "Fling-toolkit"

    private final class Box {
        let strict: Bool
        let limit: Int
        var file: FileHandle?
        var data = Data()
        var done = 0
        var total = 0
        var error: Error?
        var progress: ((Int, Int) -> Void)?
        var continuation: CheckedContinuation<(Data, Int), Error>?
        init(strict: Bool, limit: Int) { self.strict = strict; self.limit = limit }
    }

    private let lock = NSLock()
    private var boxes: [Int: Box] = [:]
    private lazy var session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.httpAdditionalHeaders = ["User-Agent": Self.userAgent]
        c.timeoutIntervalForRequest = 30
        return URLSession(configuration: c, delegate: self, delegateQueue: nil)
    }()

    public override init() { super.init() }

    private func box(_ t: URLSessionTask) -> Box? { lock.lock(); defer { lock.unlock() }; return boxes[t.taskIdentifier] }

    private func start(_ req: URLRequest, strict: Bool, limit: Int, file: FileHandle?,
                       progress: ((Int, Int) -> Void)?) async throws -> (Data, Int) {
        guard req.url?.scheme == "https" else { throw CatalogError(CatalogText.httpsOnly) }
        let b = Box(strict: strict, limit: limit)
        b.file = file; b.progress = progress
        let task = session.dataTask(with: req)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { c in
                b.continuation = c
                lock.lock(); boxes[task.taskIdentifier] = b; lock.unlock()
                task.resume()
            }
        } onCancel: { task.cancel() }
    }

    public func get(_ url: String, limit: Int, strict: Bool) async throws -> Data {
        guard let u = URL(string: url) else { throw CatalogError(CatalogText.httpsOnly) }
        return try await start(URLRequest(url: u), strict: strict, limit: limit, file: nil, progress: nil).0
    }

    public func headSize(_ url: String) async -> Int {
        guard let u = URL(string: url) else { return 0 }
        var req = URLRequest(url: u)
        req.httpMethod = "HEAD"
        return (try? await start(req, strict: true, limit: 0, file: nil, progress: nil).1) ?? 0
    }

    public func download(_ url: String, to file: URL, limit: Int,
                         progress: @escaping @Sendable (Int, Int) -> Void) async throws {
        guard let u = URL(string: url) else { throw CatalogError(CatalogText.httpsOnly) }
        FileManager.default.createFile(atPath: file.path, contents: nil)
        let h = try FileHandle(forWritingTo: file)
        defer { try? h.close() }
        do {
            _ = try await start(URLRequest(url: u, timeoutInterval: 60), strict: false, limit: limit, file: h, progress: progress)
        } catch {
            try? FileManager.default.removeItem(at: file)
            throw error
        }
    }

    // URLSession delegate

    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                           newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let b = box(task) else { completionHandler(nil); return }
        if b.strict {
            b.error = CatalogError(CatalogText.redirected)
            completionHandler(nil); task.cancel(); return
        }
        if request.url?.scheme != "https" {
            b.error = CatalogError(CatalogText.redirectNotHTTPS)
            completionHandler(nil); task.cancel(); return
        }
        completionHandler(request)
    }

    public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                           completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let b = box(dataTask) else { completionHandler(.cancel); return }
        let http = response as? HTTPURLResponse
        let code = http?.statusCode ?? 0
        if code >= 300 && code < 400 { b.error = CatalogError(b.strict ? CatalogText.redirected : CatalogText.redirectNotHTTPS) }
        else if code >= 400 { b.error = CatalogError(CatalogText.serverSaid(code)) }
        b.total = Int(max(response.expectedContentLength, 0))
        if b.limit > 0, b.total > b.limit {
            b.error = CatalogError(b.file == nil ? CatalogText.tooBig : CatalogText.archiveTooBig)
        }
        if b.error != nil { completionHandler(.cancel); return }
        b.progress?(0, b.total)
        completionHandler(.allow)
    }

    public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let b = box(dataTask), b.error == nil else { return }
        b.done += data.count
        if b.limit > 0, b.done > b.limit {
            b.error = CatalogError(b.file == nil ? CatalogText.tooBig : CatalogText.archiveTooBig)
            dataTask.cancel(); return
        }
        if let f = b.file {
            do { try f.write(contentsOf: data) } catch { b.error = error; dataTask.cancel(); return }
            b.progress?(b.done, max(b.total, b.done))
        } else {
            b.data.append(data)
        }
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock(); let b = boxes.removeValue(forKey: task.taskIdentifier); lock.unlock()
        guard let b, let c = b.continuation else { return }
        b.continuation = nil
        if let e = b.error { c.resume(throwing: e); return }
        if let error {
            if (error as NSError).code == NSURLErrorCancelled { c.resume(throwing: CancellationError()) }
            else { c.resume(throwing: CatalogError(CatalogText.offline)) }
            return
        }
        c.resume(returning: (b.data, b.total))
    }
}
