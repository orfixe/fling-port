// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port (macOS guard): decides whether Fling may start a program.
///
/// The path is resolved to the real file first (symlinks followed), so a link in
/// `~/.local/bin` or `/opt/homebrew/bin` is judged by what it points to. Then:
///
/// - **Mach-O programs** may run if they are in a folder only an administrator can change
///   (owned by root all the way up, not writable by others), or signed by Apple,
///   a Developer ID or the Mac App Store — or if you trusted these exact bytes
///   (SHA-256 pinned in ``ToolTrust``: Homebrew builds, uv's Python…).
/// - **Scripts** (`#!`): the interpreter is checked by the rules above (`/usr/bin/env x`
///   looks `x` up), and the script file itself must be in an admin-only folder or pinned.
///   `catt` from uv is such a script: its entry file and its Python are pinned together.
/// - An unsigned program or script inside a temporary folder is refused outright, even
///   when asked to trust it.
/// - Anything else (not a file, not executable, not a program) is refused.
public final class ExecutablePolicy: @unchecked Sendable {
    public enum ItemKind: String, Codable, Sendable { case program, script, interpreter }

    /// One file that would have to be trusted by its hash.
    public struct Pending: Equatable, Sendable {
        public let path: String          // real path
        public let sha256: String
        public let kind: ItemKind
        /// set when this path was trusted before with other contents (e.g. after `brew upgrade`)
        public let previousSHA256: String?
        public var isChange: Bool { previousSHA256 != nil }
    }

    public struct TrustRequest: Equatable, Sendable {
        /// the path as Fling was asked to run it
        public let executable: String
        public let items: [Pending]
        public var isChange: Bool { items.contains { $0.isChange } }
        public var key: String { items.map { "\($0.path)#\($0.sha256)" }.joined(separator: ";") }
        public var displayName: String { (executable as NSString).lastPathComponent }
    }

    public enum Verdict: Equatable, Sendable {
        case allowed(String)
        case needsTrust(TrustRequest)
        /// reason: a fixed English phrase (translated by the UI); see `Reason`
        case refused(String)

        public var isAllowed: Bool { if case .allowed = self { return true } else { return false } }
    }

    /// The refusal phrases (fixed, so the UI can translate them).
    public enum Reason {
        public static let notAbsolute = "it is not given by its full path"
        public static let missing = "the file does not exist"
        public static let notExecutable = "it is not an executable file"
        public static let unknownFormat = "it is neither a program nor a script"
        public static let inTemp = "an unsigned program in a temporary folder"
        public static let badShebang = "the script names no interpreter Fling can check"
        public static let nestedScript = "the script's interpreter is itself a script"
    }

    public let trust: ToolTrust
    let signatures: CodeSignatureChecking
    /// prefixes (real paths, ending in "/") where unsigned code is never accepted
    let untrustedRoots: [String]
    let searchPath: [String]
    private let lock = NSLock()
    private var hashCache: [FileStamp: String] = [:]
    private var signatureCache: [FileStamp: CodeTrust] = [:]

    public init(trust: ToolTrust, signatures: CodeSignatureChecking = SecCodeSignatureChecker(),
                untrustedRoots: [String]? = nil, searchPath: [String]? = nil) {
        self.trust = trust
        self.signatures = signatures
        self.untrustedRoots = (untrustedRoots ?? Self.defaultUntrustedRoots).map { $0.hasSuffix("/") ? $0 : $0 + "/" }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        self.searchPath = searchPath ?? ["/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin",
                                         "/usr/local/bin", "\(home)/.local/bin"]
    }

    static var defaultUntrustedRoots: [String] {
        var roots = ["/private/tmp", "/private/var/tmp", "/private/var/folders", "/Volumes"]
        if let t = realPath(NSTemporaryDirectory()) { roots.append(t) }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        roots += ["\(home)/Downloads", "\(home)/Library/Caches", "\(home)/.Trash"]
        return roots.compactMap { realPath($0) ?? $0 }
    }

    // MARK: - evaluation

    public func evaluate(_ executable: String) -> Verdict {
        evaluate(executable, depth: 0)
    }

    private func evaluate(_ executable: String, depth: Int) -> Verdict {
        guard executable.hasPrefix("/") else { return .refused(Reason.notAbsolute) }
        guard let real = Self.realPath(executable), let st = Self.fileStat(real) else { return .refused(Reason.missing) }
        guard (st.st_mode & S_IFMT) == S_IFREG, (st.st_mode & 0o111) != 0 else { return .refused(Reason.notExecutable) }
        let stamp = FileStamp(path: real, st)
        let head = Self.head(real)
        let inTemp = untrustedRoots.contains { real.hasPrefix($0) }

        if Self.isMachO(head) {
            if Self.isAdminOnly(real) { return .allowed("\(real): in a folder only an administrator can change") }
            switch cachedSignature(stamp) {
            case .apple: return .allowed("\(real): signed by Apple")
            case .developerID(let team): return .allowed("\(real): Developer ID \(team)")
            case .appStore(let team): return .allowed("\(real): Mac App Store \(team)")
            case .none: break
            }
            if inTemp { return .refused(Reason.inTemp) }
            let kind: ItemKind = depth == 0 ? .program : .interpreter
            if pinned(real, stamp, kind: kind) != nil { return .allowed("\(real): trusted by its SHA-256") }
            return pendingVerdict(executable, [pending(real, stamp, kind)])
        }

        guard head.starts(with: [0x23, 0x21]) else { return .refused(Reason.unknownFormat) }   // "#!"
        if depth > 0 { return .refused(Reason.nestedScript) }
        guard let interpreter = interpreterPath(head) else { return .refused(Reason.badShebang) }
        var items: [Pending] = []
        switch evaluate(interpreter, depth: 1) {
        case .refused(let why): return .refused(why == Reason.nestedScript ? why : Reason.badShebang)
        case .needsTrust(let r): items += r.items
        case .allowed: break
        }
        if !Self.isAdminOnly(real) {
            if inTemp { return .refused(Reason.inTemp) }
            if pinned(real, stamp, kind: .script) == nil { items.append(pending(real, stamp, .script)) }
        }
        return items.isEmpty ? .allowed("\(real): script, interpreter \(interpreter)") : pendingVerdict(executable, items)
    }

    private func pendingVerdict(_ executable: String, _ items: [Pending]) -> Verdict {
        .needsTrust(TrustRequest(executable: executable, items: items))
    }

    private func pending(_ real: String, _ stamp: FileStamp, _ kind: ItemKind) -> Pending {
        Pending(path: real, sha256: cachedHash(stamp) ?? "", kind: kind,
                previousSHA256: trust.pin(for: real)?.sha256)
    }

    /// non-nil when `real` is pinned with exactly its current contents
    private func pinned(_ real: String, _ stamp: FileStamp, kind: ItemKind) -> ToolTrust.Pin? {
        guard let pin = trust.pin(for: real), let sha = cachedHash(stamp), pin.sha256 == sha else { return nil }
        return pin
    }

    private func cachedHash(_ stamp: FileStamp) -> String? {
        lock.lock()
        if let h = hashCache[stamp] { lock.unlock(); return h }
        lock.unlock()
        guard let h = FileHash.sha256(URL(fileURLWithPath: stamp.path)) else { return nil }
        lock.lock(); hashCache[stamp] = h; lock.unlock()
        return h
    }

    private func cachedSignature(_ stamp: FileStamp) -> CodeTrust {
        lock.lock()
        if let t = signatureCache[stamp] { lock.unlock(); return t }
        lock.unlock()
        let t = signatures.trust(ofFile: stamp.path)
        lock.lock(); signatureCache[stamp] = t; lock.unlock()
        return t
    }

    /// `#!/path/interp [arg]`, `#!/usr/bin/env [-S] name …` → the interpreter's absolute path.
    func interpreterPath(_ head: [UInt8]) -> String? {
        let line = head.dropFirst(2).prefix { $0 != 0x0A && $0 != 0x0D }
        guard let text = String(bytes: line, encoding: .utf8) else { return nil }
        let words = text.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
        guard let first = words.first, first.hasPrefix("/") else { return nil }
        guard (first as NSString).lastPathComponent == "env", Self.realPath(first) == "/usr/bin/env" else { return first }
        // env: the first word that is not an option or a VAR=value assignment
        guard let name = words.dropFirst().first(where: { !$0.hasPrefix("-") && !$0.contains("=") }) else { return nil }
        if name.hasPrefix("/") { return name }
        guard !name.contains("/") else { return nil }
        return searchPath.map { "\($0)/\(name)" }.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    // MARK: - file facts

    struct FileStamp: Hashable {
        let path: String
        let dev: Int32, ino: UInt64, size: Int64
        let mtime: Int, mtimeNs: Int, ctime: Int, ctimeNs: Int
        init(path: String, _ st: Darwin.stat) {
            self.path = path
            dev = st.st_dev; ino = st.st_ino; size = st.st_size
            mtime = st.st_mtimespec.tv_sec; mtimeNs = st.st_mtimespec.tv_nsec
            ctime = st.st_ctimespec.tv_sec; ctimeNs = st.st_ctimespec.tv_nsec
        }
    }

    public static func realPath(_ path: String) -> String? {
        guard let p = Darwin.realpath(path, nil) else { return nil }
        defer { free(p) }
        return String(cString: p)
    }

    static func fileStat(_ path: String, follow: Bool = true) -> Darwin.stat? {
        var st = Darwin.stat()
        let r = follow ? stat(path, &st) : lstat(path, &st)
        return r == 0 ? st : nil
    }

    static func head(_ path: String, count: Int = 512) -> [UInt8] {
        guard let h = FileHandle(forReadingAtPath: path) else { return [] }
        defer { try? h.close() }
        return Array((try? h.read(upToCount: count)) ?? Data())
    }

    static func isMachO(_ head: [UInt8]) -> Bool {
        guard head.count >= 4 else { return false }
        let m = Array(head.prefix(4))
        return [[0xFE, 0xED, 0xFA, 0xCE], [0xFE, 0xED, 0xFA, 0xCF], [0xCE, 0xFA, 0xED, 0xFE],
                [0xCF, 0xFA, 0xED, 0xFE], [0xCA, 0xFE, 0xBA, 0xBE], [0xBE, 0xBA, 0xFE, 0xCA]].contains(m)
    }

    /// The file and every folder above it belong to root and nobody else may write them
    /// (group-writable only for group wheel): changing it needs an administrator password.
    /// `/Applications` (group admin) does not count: an admin user writes there freely.
    public static func isAdminOnly(_ real: String) -> Bool {
        var p = real
        while true {
            guard let st = fileStat(p, follow: false) else { return false }
            if (st.st_mode & S_IFMT) == S_IFLNK { return false }   // real paths have no links
            if st.st_uid != 0 || (st.st_mode & 0o002) != 0 { return false }
            if (st.st_mode & 0o020) != 0 && st.st_gid != 0 { return false }
            if p == "/" { return true }
            p = (p as NSString).deletingLastPathComponent
            if p.isEmpty { p = "/" }
        }
    }
}
