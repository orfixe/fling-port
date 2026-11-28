// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Security

/// Who vouches for a program, by its code signature (checked with the Security framework,
/// offline: no revocation lookup).
public enum CodeTrust: Equatable, Sendable {
    /// part of macOS or another Apple binary (`anchor apple`)
    case apple
    /// Developer ID, a developer identified and notarizable by Apple
    case developerID(team: String)
    /// Mac App Store
    case appStore(team: String)
    /// unsigned, ad-hoc / locally signed, broken or otherwise unknown
    case none
}

public protocol CodeSignatureChecking: Sendable {
    func trust(ofFile path: String) -> CodeTrust
}

public struct SecCodeSignatureChecker: CodeSignatureChecking {
    public init() {}

    // Apple's documented designated-requirement forms
    private static let appleReq = req("anchor apple")
    private static let developerIDReq = req("anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists")
    private static let appStoreReq = req("anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.9] exists")

    private static func req(_ s: String) -> SecRequirement? {
        var r: SecRequirement?
        return SecRequirementCreateWithString(s as CFString, [], &r) == errSecSuccess ? r : nil
    }

    public func trust(ofFile path: String) -> CodeTrust {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(URL(fileURLWithPath: path) as CFURL, [], &code) == errSecSuccess,
              let code else { return .none }
        let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate)
        func passes(_ r: SecRequirement?) -> Bool {
            guard let r else { return false }
            return SecStaticCodeCheckValidity(code, flags, r) == errSecSuccess
        }
        if passes(Self.appleReq) { return .apple }
        if passes(Self.developerIDReq) { return .developerID(team: Self.teamID(code) ?? "?") }
        if passes(Self.appStoreReq) { return .appStore(team: Self.teamID(code) ?? "?") }
        return .none
    }

    static func teamID(_ code: SecStaticCode) -> String? {
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
              let dict = info as? [String: Any] else { return nil }
        return dict[kSecCodeInfoTeamIdentifier as String] as? String
    }
}
