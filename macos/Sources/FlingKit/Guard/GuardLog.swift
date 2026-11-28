// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port (macOS guard): `~/Library/Logs/Fling Port/guard.log`, rotated at 1 MB
/// (guard.log.1, guard.log.2 kept), like `%LOCALAPPDATA%\Fling\logs\guard.log` on Windows.
public final class GuardLog: @unchecked Sendable {
    public static let shared = GuardLog(directory: GuardLog.defaultDirectory)

    /// Running inside a test runner (XCTest, or Swift Testing via xctest / swiftpm-testing-helper).
    static let underTest: Bool = {
        let name = ProcessInfo.processInfo.processName
        return NSClassFromString("XCTestCase") != nil || name == "xctest" || name == "swiftpm-testing-helper"
            || CommandLine.arguments.contains { $0.hasSuffix(".xctest") || $0.contains(".xctest/") }
    }()

    /// Unit tests never write into the user's real log.
    static var defaultDirectory: URL {
        if underTest {
            return FileManager.default.temporaryDirectory.appendingPathComponent("fling-guard-tests-log")
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Fling Port", isDirectory: true)
    }

    public let directory: URL
    public let maxBytes: Int
    public let keep: Int
    private let lock = NSLock()
    private var onceKeys = Set<String>()

    public init(directory: URL, maxBytes: Int = 1 << 20, keep: Int = 2) {
        self.directory = directory
        self.maxBytes = maxBytes
        self.keep = keep
    }

    public var file: URL { directory.appendingPathComponent("guard.log") }

    public func info(_ message: String) { write("info", message) }
    public func warn(_ message: String) { write("WARN", message) }
    public func alert(_ message: String) { write("ALERT", message) }

    /// Logs `message` only the first time `key` is seen in this run (polling would
    /// otherwise repeat the same verdict every few seconds).
    public func once(_ key: String, _ level: String = "info", _ message: String) {
        lock.lock()
        let fresh = onceKeys.insert(key).inserted
        lock.unlock()
        if fresh { write(level, message) }
    }

    private static let stamp: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    func write(_ level: String, _ message: String) {
        let line = "\(Self.stamp.string(from: Date())) [\(level)] \(message.replacingOccurrences(of: "\n", with: " | "))\n"
        lock.lock(); defer { lock.unlock() }
        let fm = FileManager.default
        try? fm.createDirectory(at: directory, withIntermediateDirectories: true)
        if let size = (try? fm.attributesOfItem(atPath: file.path))?[.size] as? Int, size + line.utf8.count > maxBytes {
            rotate()
        }
        if let h = FileHandle(forWritingAtPath: file.path) {
            h.seekToEndOfFile()
            h.write(Data(line.utf8))
            try? h.close()
        } else {
            fm.createFile(atPath: file.path, contents: Data(line.utf8), attributes: [.posixPermissions: 0o600])
        }
    }

    private func rotate() {
        let fm = FileManager.default
        let name = { (i: Int) in self.directory.appendingPathComponent("guard.log.\(i)") }
        try? fm.removeItem(at: name(keep))
        if keep > 1 {
            for i in stride(from: keep - 1, through: 1, by: -1) { try? fm.moveItem(at: name(i), to: name(i + 1)) }
        }
        if keep > 0 { try? fm.moveItem(at: file, to: name(1)) } else { try? fm.removeItem(at: file) }
    }
}
