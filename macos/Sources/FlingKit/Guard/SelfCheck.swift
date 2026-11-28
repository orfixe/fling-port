// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Security

/// Port (macOS guard): Fling's check of itself at every start.
///
/// 1. The code signature of the whole bundle (Security framework, strict): any changed,
///    added or removed file in `Fling Port.app` breaks it.
/// 2. Who signed it: this Mac's own "Fling Port Local" identity or ad-hoc (`codesign -s -`).
///    Anything else is not how Fling Port is installed.
/// 3. `Contents/Resources/install-receipt.plist`, written by `macos/install.sh` after the
///    download matched the signed `SHA256SUMS.txt` (and sealed by the signature): it says
///    the install was verified, and which release it was.
/// 4. The install record `~/Library/Application Support/Fling Port/install.plist`, written by
///    install.sh *after* signing (the signature changes the program, and a sealed file can't
///    hold the hash of what seals it): the SHA-256 of the signed program, compared with the
///    program on disk. Re-signing the bundle with changed code no longer matches it.
///
/// With a verified receipt the guard **enforces** (a problem here → alert and quit; unknown
/// programs are refused or asked about). Without one — a build from source — it only reports.
public enum SelfCheck {
    public static let receiptName = "install-receipt.plist"
    public static let localIdentity = "Fling Port Local"

    public struct Receipt: Codable, Equatable {
        public let verified: Bool
        public let version: String?
        /// the downloaded program, as listed in the signed SHA256SUMS.txt (before local signing)
        public let releaseSHA256: String?
        public let installedAt: String?
    }

    /// Outside the bundle, written after signing.
    public struct InstallRecord: Codable, Equatable {
        public let binarySHA256: String
        public let app: String?
    }

    /// Under `$HOME`, like install.sh writes it (the same folder as the real home for an app
    /// started normally).
    public static var defaultRecord: URL {
        let home = ProcessInfo.processInfo.environment["HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent("Library/Application Support/Fling Port/install.plist")
    }

    public struct Result: Equatable {
        public let mode: GuardMode
        /// fixed English phrases (see `Problem`), empty when all is well
        public let problems: [String]
        /// technical details for the log
        public let details: [String]
        public let signer: String
        public let hardenedRuntime: Bool
        public let receipt: Receipt?
        public var tampered: Bool { !problems.isEmpty }
    }

    public enum Problem {
        public static let signatureBroken = "its files were changed after it was installed"
        public static let unsigned = "it is not signed"
        public static let otherSigner = "it is signed by someone else"
        public static let binaryChanged = "the program is not the one that was installed"
        public static let receiptUnreadable = "its install record can't be read"
        public static let recordMissing = "its install record is missing"
    }

    /// `enforceOverride`: a source build can opt into enforcing (`defaults write
    /// app.orfixe.flingport guardMode enforce`); a verified install can't opt out.
    public static func run(bundle: URL = Bundle.main.bundleURL,
                           executable: URL? = Bundle.main.executableURL,
                           record: URL = SelfCheck.defaultRecord,
                           enforceOverride: Bool = UserDefaults.standard.string(forKey: "guardMode") == "enforce") -> Result {
        var problems: [String] = []
        var details: [String] = []
        var signer = "?"
        var hardened = false

        var code: SecStaticCode?
        if SecStaticCodeCreateWithPath(bundle as CFURL, [], &code) == errSecSuccess, let code {
            let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate | kSecCSCheckNestedCode)
            let status = SecStaticCodeCheckValidity(code, flags, nil)
            if status == errSecCSUnsigned {
                problems.append(Problem.unsigned)
                details.append("not signed")
            } else if status != errSecSuccess {
                problems.append(Problem.signatureBroken)
                details.append("code signature invalid: OSStatus \(status) \(SecCopyErrorMessageString(status, nil) as String? ?? "")")
            }
            var info: CFDictionary?
            if SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
               let dict = info as? [String: Any] {
                let csFlags = (dict[kSecCodeInfoFlags as String] as? NSNumber)?.uint32Value ?? 0
                hardened = csFlags & 0x10000 != 0           // kSecCodeSignatureRuntime
                if csFlags & 0x2 != 0 {                      // kSecCodeSignatureAdhoc
                    signer = "ad-hoc"
                } else if let certs = dict[kSecCodeInfoCertificates as String] as? [SecCertificate], let leaf = certs.first {
                    signer = (SecCertificateCopySubjectSummary(leaf) as String?) ?? "?"
                } else if status != errSecCSUnsigned {
                    signer = "unknown"
                }
                if status != errSecCSUnsigned, signer != "ad-hoc", signer != localIdentity {
                    problems.append(Problem.otherSigner)
                    details.append("signed by \(signer)")
                }
            }
        } else {
            problems.append(Problem.unsigned)
            details.append("can't read the code signature of \(bundle.path)")
        }

        var receipt: Receipt?
        let receiptURL = bundle.appendingPathComponent("Contents/Resources/\(receiptName)")
        if FileManager.default.fileExists(atPath: receiptURL.path) {
            if let data = try? Data(contentsOf: receiptURL), let r = try? PropertyListDecoder().decode(Receipt.self, from: data) {
                receipt = r
                if r.verified, let exe = executable {
                    if let data = try? Data(contentsOf: record),
                       let rec = try? PropertyListDecoder().decode(InstallRecord.self, from: data) {
                        let got = FileHash.sha256(exe)
                        if got != rec.binarySHA256.lowercased() {
                            problems.append(Problem.binaryChanged)
                            details.append("program SHA-256 \(got ?? "unreadable"), installed \(rec.binarySHA256)")
                        }
                    } else {
                        problems.append(Problem.recordMissing)
                        details.append("no readable \(record.path)")
                    }
                }
            } else {
                problems.append(Problem.receiptUnreadable)
                details.append("bad \(receiptURL.path)")
            }
        }
        // an unreadable record is treated as a verified install: breaking it must not switch the guard off
        let verifiedInstall = receipt?.verified == true || problems.contains(Problem.receiptUnreadable)
        let mode: GuardMode = verifiedInstall || enforceOverride ? .enforce : .reportOnly
        return Result(mode: mode, problems: problems, details: details, signer: signer,
                      hardenedRuntime: hardened, receipt: receipt)
    }
}
