// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: the Catalog talks to the TV through the same adb, the same adb server (port 15037)
/// and the same remembered per-TV port as the ADB panel — not a second adb on 5037 / 5555.
public struct ADBClientCatalogADB: CatalogADB {
    public init() {}

    private static func client() -> ADBClient? {
        ADBLocator.find().map { ADBClient(executable: $0) }
    }

    public func ready(ip: String, connect: Bool) async -> String? {
        guard let adb = Self.client() else { return CatalogInstaller.adbMissing }
        return await Task.detached { () -> String? in
            if connect {
                let host = ip.split(separator: ":").first.map(String.init) ?? ip
                switch adb.reach(ip: host, announce: adb.mdns()[host] ?? ADBAnnounce()) {
                case .device: return nil
                case .unauthorized: return CatalogInstaller.adbUnauthorized
                case .offline, .closed: return CatalogInstaller.adbNotConnected
                }
            }
            switch adb.stateNow(ip: ip)?.state {
            case "device"?: return nil
            case "unauthorized"?: return CatalogInstaller.adbUnauthorized
            default: return CatalogInstaller.adbNotConnected
            }
        }.value
    }

    public func run(ip: String, args: [String], timeout: TimeInterval) async -> ProcessResult {
        guard let adb = Self.client() else {
            return ProcessResult(exitCode: -1, stdout: "", stderr: CatalogInstaller.adbMissing)
        }
        return await Task.detached {
            let serial = ip.contains(":") ? ip : adb.serial(ip)
            return (try? adb.adb(["-s", serial] + args, timeout: timeout))
                ?? ProcessResult(exitCode: -1, stdout: "", stderr: "adb did not start")
        }.value
    }
}
