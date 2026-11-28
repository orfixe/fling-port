// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port (macOS guard): command-line modes of the Fling binary (no UI, handled in main.swift
/// before the app starts).
///
///   Fling --verify-release SHA256SUMS.txt [SHA256SUMS.txt.minisig] [file…]
///       the list is signed with the release key, and each file matches it (by its name);
///       prints "OK version=<v>", exit 0 — exit 1 otherwise. install.sh uses an already
///       installed, verified Fling Port for this when minisign isn't there.
///   Fling --guard-check <program>…     what the guard thinks of each program (exit 0)
///   Fling --guard-selfcheck            the start-up self check, printed
public enum GuardCLI {
    /// nil when `args` asks for no CLI mode; otherwise the exit code.
    public static func handle(_ args: [String], out: (String) -> Void = { print($0) },
                              err: (String) -> Void = { FileHandle.standardError.write(Data(($0 + "\n").utf8)) }) -> Int32? {
        guard args.count >= 2 else { return nil }
        let rest = Array(args.dropFirst(2))
        switch args[1] {
        case "--verify-release":
            return verifyRelease(rest, out: out, err: err)
        case "--guard-check":
            for p in rest {
                switch ProcessGuard.shared.policy.evaluate(p) {
                case .allowed(let why): out("allowed  \(p): \(why)")
                case .refused(let why): out("refused  \(p): \(why)")
                case .needsTrust(let r):
                    out("ask      \(p): " + r.items.map { "\($0.kind.rawValue) \($0.path) \($0.sha256)" +
                        ($0.isChange ? " (changed)" : "") }.joined(separator: ", "))
                }
            }
            return 0
        case "--guard-selfcheck":
            let r = SelfCheck.run()
            out("mode=\(r.mode.rawValue) signer=\(r.signer) hardenedRuntime=\(r.hardenedRuntime) " +
                "receipt=\(r.receipt.map { "verified=\($0.verified) version=\($0.version ?? "?")" } ?? "none")")
            for d in r.details { out("problem: \(d)") }
            return r.tampered ? 1 : 0
        default:
            return nil
        }
    }

    static func verifyRelease(_ rest: [String], out: (String) -> Void, err: (String) -> Void) -> Int32 {
        guard let sumsPath = rest.first else {
            err("usage: Fling --verify-release SHA256SUMS.txt [SHA256SUMS.txt.minisig] [file…]")
            return 64
        }
        var files = Array(rest.dropFirst())
        var sigPath = sumsPath + ".minisig"
        if let f = files.first, f.hasSuffix(".minisig") { sigPath = f; files.removeFirst() }
        do {
            let sums = try Data(contentsOf: URL(fileURLWithPath: sumsPath))
            let sig = try String(contentsOfFile: sigPath, encoding: .utf8)
            let list = try ReleaseSums(sums: sums, signature: sig)
            var bad = false
            for f in files {
                if let why = list.mismatch(file: URL(fileURLWithPath: f)) { err("NOT OK: \(why)"); bad = true }
            }
            if bad { return 1 }
            out("OK version=\(list.version ?? "?")")
            return 0
        } catch {
            err("NOT OK: \(error)")
            return 1
        }
    }
}
