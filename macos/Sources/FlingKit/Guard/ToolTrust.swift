// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port (macOS guard): the programs you trusted by their exact contents — the macOS
/// counterpart of the Tools-registered tools on Windows. Each entry is a real path plus
/// the SHA-256 it had when you installed, picked or allowed it; the same path with other
/// bytes (e.g. after `brew upgrade`) is asked about again.
///
/// Kept in `~/Library/Application Support/Fling Port/trusted-tools.json` (mode 0600).
public final class ToolTrust: @unchecked Sendable {
    public struct Pin: Codable, Equatable, Sendable {
        public let path: String
        public let sha256: String
        public let kind: String
        public let addedAt: Date
        /// how it got here: "asked" (you allowed it in the prompt), "tools" (Tools window), …
        public let source: String
    }

    public struct RegisterError: Error, Equatable, CustomStringConvertible {
        public let description: String
    }

    public static let shared = ToolTrust(file: ToolTrust.defaultFile)

    static var defaultFile: URL {
        if GuardLog.underTest {   // tests never touch the user's list
            return FileManager.default.temporaryDirectory.appendingPathComponent("fling-guard-tests-trust.json")
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Fling Port/trusted-tools.json")
    }

    public let file: URL
    private let lock = NSLock()
    private var pins: [String: Pin]

    public init(file: URL) {
        self.file = file
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let list = (try? Data(contentsOf: file)).flatMap { try? decoder.decode([Pin].self, from: $0) } ?? []
        pins = Dictionary(list.map { ($0.path, $0) }, uniquingKeysWith: { _, b in b })
    }

    public var all: [Pin] {
        lock.lock(); defer { lock.unlock() }
        return pins.values.sorted { $0.path < $1.path }
    }

    public func pin(for realPath: String) -> Pin? {
        lock.lock(); defer { lock.unlock() }
        return pins[realPath]
    }

    /// Pins the files of a trust request (the user said yes, or the Tools window vouches).
    public func add(_ items: [ExecutablePolicy.Pending], source: String) {
        lock.lock()
        for i in items where !i.sha256.isEmpty {
            pins[i.path] = Pin(path: i.path, sha256: i.sha256, kind: i.kind.rawValue, addedAt: Date(), source: source)
        }
        let snapshot = Array(pins.values)
        lock.unlock()
        save(snapshot)
        for i in items {
            GuardLog.shared.info("trusted \(i.kind.rawValue) \(i.path) sha256=\(i.sha256) (\(source))")
        }
    }

    /// Forgets a path (the given path or what it resolves to).
    public func remove(path: String) {
        let real = ExecutablePolicy.realPath(path) ?? path
        lock.lock()
        let removed = pins.removeValue(forKey: real) ?? pins.removeValue(forKey: path)
        let snapshot = Array(pins.values)
        lock.unlock()
        if removed != nil {
            save(snapshot)
            GuardLog.shared.info("forgot \(real)")
        }
    }

    private func save(_ list: [Pin]) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(list.sorted { $0.path < $1.path }) else { return }
        try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: file, options: .atomic)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }

    // MARK: - API for the Tools window (and anything else that installs a tool)

    /// Registers a tool the user just installed or picked, so Fling may start it without
    /// asking. Call it right after the install, from the code that installed it.
    ///
    /// - `expectedSHA256`: the publisher's checksum of the program, when there is one; the
    ///   file on disk must match it or nothing is registered.
    /// - Returns the entries that were pinned — empty when the tool needs no pin (signed
    ///   by Apple / a Developer ID, or in an admin-only folder).
    /// - Throws when the guard would refuse the file whatever the user says (missing,
    ///   not a program, unsigned inside a temporary folder, checksum mismatch).
    @discardableResult
    public static func register(path: String, expectedSHA256: String? = nil, source: String = "tools",
                                guard g: ProcessGuard = .shared) throws -> [Pin] {
        if let want = expectedSHA256?.lowercased() {
            let real = ExecutablePolicy.realPath(path) ?? path
            guard let got = FileHash.sha256(URL(fileURLWithPath: real)), got == want else {
                throw RegisterError(description: "\(path) does not match the publisher's checksum")
            }
        }
        switch g.policy.evaluate(path) {
        case .allowed:
            return []
        case .refused(let why):
            throw RegisterError(description: "\(path): \(why)")
        case .needsTrust(let request):
            g.policy.trust.add(request.items, source: source)
            return request.items.compactMap { g.policy.trust.pin(for: $0.path) }
        }
    }

    /// Forgets a registered tool (e.g. the Tools window uninstalled it).
    public static func unregister(path: String, guard g: ProcessGuard = .shared) {
        g.policy.trust.remove(path: path)
    }
}
