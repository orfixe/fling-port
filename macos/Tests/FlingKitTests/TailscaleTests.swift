// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Testing
@testable import FlingKit

/// Port: remote access over Tailscale (Tailscale.swift). swift-testing, so it also runs
/// with the Command Line Tools alone (they ship Testing.framework but no XCTest).
@Suite struct TailscaleTests {

    /// Trimmed from a real `tailscale status --json` (macOS client 1.9x), addresses changed.
    static let sample = #"""
    {
      "Version": "1.90.6-t1234abcd",
      "TUN": false,
      "BackendState": "Running",
      "HaveNodeKey": true,
      "AuthURL": "",
      "TailscaleIPs": ["100.101.1.2", "fd7a:115c:a1e0::1a01:102"],
      "Self": {
        "ID": "nSelf1CNTRL", "PublicKey": "nodekey:aaaa", "HostName": "MacBook-Air",
        "DNSName": "macbook-air.tail1234.ts.net.", "OS": "macOS", "UserID": 123456,
        "TailscaleIPs": ["100.101.1.2", "fd7a:115c:a1e0::1a01:102"],
        "Relay": "fra", "CurAddr": "", "Online": true, "Active": false
      },
      "MagicDNSSuffix": "tail1234.ts.net",
      "CurrentTailnet": {"Name": "user@example.com", "MagicDNSSuffix": "tail1234.ts.net", "MagicDNSEnabled": true},
      "Peer": {
        "nodekey:bbbb": {
          "ID": "nTV1CNTRL", "PublicKey": "nodekey:bbbb", "HostName": "BRAVIA-4K-VH2",
          "DNSName": "bravia-4k-vh2.tail1234.ts.net.", "OS": "android", "UserID": 123456,
          "TailscaleIPs": ["100.88.7.6", "fd7a:115c:a1e0::5d01:706"],
          "AllowedIPs": ["100.88.7.6/32", "fd7a:115c:a1e0::5d01:706/128"],
          "Relay": "fra", "CurAddr": "203.0.113.7:41641", "Online": true, "Active": true
        },
        "nodekey:cccc": {
          "ID": "nPhone1CNTRL", "PublicKey": "nodekey:cccc", "HostName": "Pixel-8",
          "DNSName": "pixel-8.tail1234.ts.net.", "OS": "android", "UserID": 123456,
          "TailscaleIPs": ["100.77.1.1"], "Relay": "ams", "CurAddr": "", "Online": false
        },
        "nodekey:dddd": {
          "ID": "nPi1CNTRL", "PublicKey": "nodekey:dddd", "HostName": "raspberrypi",
          "DNSName": "raspberrypi.tail1234.ts.net.", "OS": "linux", "UserID": 123456,
          "TailscaleIPs": ["100.66.5.4"],
          "AllowedIPs": ["100.66.5.4/32", "192.168.1.0/24"],
          "PrimaryRoutes": ["192.168.1.0/24"],
          "Relay": "fra", "CurAddr": "", "Online": true
        },
        "nodekey:eeee": {
          "ID": "nBox1CNTRL", "PublicKey": "nodekey:eeee", "HostName": "localhost",
          "DNSName": "living-room.tail1234.ts.net.", "OS": "android", "UserID": 123456,
          "TailscaleIPs": ["100.99.9.9"], "Relay": "fra", "CurAddr": "", "Online": true
        }
      },
      "User": {"123456": {"ID": 123456, "LoginName": "user@example.com", "DisplayName": "User"}},
      "ClientVersion": null
    }
    """#

    @Test func test_parses_sample_status() throws {
        let s = try TailscaleStatusParser.parse(Data(Self.sample.utf8))
        #expect(s.running)
        #expect(s.login == "user@example.com")
        #expect(s.magicDNSSuffix == "tail1234.ts.net")
        #expect(s.me?.host == "MacBook-Air")
        #expect(s.me?.ip == "100.101.1.2")
        #expect(s.peers.count == 4)

        // TV-looking first, then the rest
        #expect(s.peers.first?.host == "BRAVIA-4K-VH2")
        let tv = try #require(s.peer(matching: "bravia-4k-vh2"))
        #expect(tv.ip == "100.88.7.6")
        #expect(tv.dns == "bravia-4k-vh2.tail1234.ts.net")
        #expect(tv.tvScore == 2)
        #expect(tv.online)
        #expect(tv.direct)
        #expect(tv.routes.isEmpty)

        // "localhost" falls back to the MagicDNS short name; an unnamed Android box may be a TV
        let box = try #require(s.peer(matching: "100.99.9.9"))
        #expect(box.host == "living-room")
        #expect(box.tvScore == 1)

        #expect(s.peer(matching: "pixel-8.tail1234.ts.net.")?.tvScore == 0)
        #expect(Set(s.tvs.map(\.host)) == ["BRAVIA-4K-VH2", "living-room"])

        // subnet routes: the home network behind the Raspberry Pi
        #expect(s.subnetRouters.map(\.host) == ["raspberrypi"])
        #expect(s.router(for: "192.168.1.42")?.host == "raspberrypi")
        #expect(s.router(for: "192.168.2.42") == nil)
    }

    @Test func test_stopped_and_garbage() throws {
        let stopped = #"{"BackendState":"Stopped","Self":{"HostName":"mac","TailscaleIPs":[]},"Peer":null}"#
        let s = try TailscaleStatusParser.parse(Data(stopped.utf8))
        #expect(!s.running)
        #expect(s.me == nil)
        #expect(s.peers.isEmpty)
        #expect(throws: TailscaleParseError.self) { try TailscaleStatusParser.parse(Data("Tailscale is stopped.".utf8)) }
    }

    @Test func test_cli_state_reads_status_json() {
        let r = StubRunner()
        r.stubbedOutput = Self.sample
        let cli = TailscaleCLI(executable: "/Applications/Tailscale.app/Contents/MacOS/Tailscale", runner: r)
        guard case .running(let s) = cli.state() else { Issue.record("expected running"); return }
        #expect(s.tvs.count == 2)
        #expect(r.calls.first?.args == ["status", "--json"])   // read-only
    }

    @Test func test_cli_state_off_and_unreadable() {
        let r = StubRunner()
        let cli = TailscaleCLI(executable: "/usr/local/bin/tailscale", runner: r)
        r.stubbedOutput = #"{"BackendState":"NeedsLogin","Peer":{}}"#
        #expect(cli.state() == .off(backendState: "NeedsLogin"))
        r.stubbedOutput = ""
        r.stubbedStderr = "Tailscale is stopped."
        r.stubbedExitCode = 1
        #expect(cli.state() == .off(backendState: "Stopped"))
        r.stubbedStderr = "something odd"
        #expect(cli.state() == .unreadable("something odd"))
    }

    @Test func test_resolve_executable_order() {
        #expect(TailscaleCLI.resolveExecutable { $0.hasPrefix("/Applications/") }
                == "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        #expect(TailscaleCLI.resolveExecutable { _ in true } == "/opt/homebrew/bin/tailscale")
        #expect(TailscaleCLI.resolveExecutable { _ in false } == nil)
    }

    @Test func test_ping_parse() {
        let relayed = TailnetPing.parse("pong from bravia (100.88.7.6) via DERP(fra) in 45ms")
        #expect(relayed == TailnetPing(ms: 45, direct: false, relay: "fra"))
        let direct = TailnetPing.parse("pong from bravia (100.88.7.6) via 203.0.113.7:41641 in 3.5ms")
        #expect(direct == TailnetPing(ms: 3.5, direct: true, relay: ""))
        #expect(TailnetPing.parse("ping \"100.88.7.6\" timed out") == nil)
    }

    @Test func test_addresses() {
        #expect(Tailnet.isTailnetIP("100.64.0.1"))
        #expect(Tailnet.isTailnetIP("100.127.255.254"))
        #expect(!Tailnet.isTailnetIP("100.128.0.1"))
        #expect(!Tailnet.isTailnetIP("192.168.1.10"))
        #expect(Tailnet.isTailnetIP("fd7a:115c:a1e0::5d01:706"))
        #expect(Tailnet.wantsTailscale("100.88.7.6"))
        #expect(Tailnet.wantsTailscale("bravia-4k-vh2.tail1234.ts.net"))
        #expect(Tailnet.wantsTailscale("tv"))
        #expect(!Tailnet.wantsTailscale("192.168.1.10"))
        #expect(!Tailnet.wantsTailscale("not a host"))
        #expect(Tailnet.cidr("192.168.1.0/24", contains: "192.168.1.200"))
        #expect(!Tailnet.cidr("192.168.1.0/24", contains: "192.168.0.200"))
        #expect(Tailnet.cidr("10.0.0.0/8", contains: "10.20.30.40"))
    }

    @Test func test_tv_score() {
        #expect(Tailnet.tvScore(host: "SHIELD", os: "android") == 2)
        #expect(Tailnet.tvScore(host: "Galaxy-S24", os: "android") == 0)
        #expect(Tailnet.tvScore(host: "galaxy-tv", os: "android") == 1)
        #expect(Tailnet.tvScore(host: "living-room-tv", os: "linux") == 0)
    }

    @Test func test_store_merge_and_cast_block() throws {
        let suite = "fling.tests.tailnet.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = TailnetTVStore(defaults: defaults)
        store.save(TailnetTV(ip: "100.88.7.6", name: "BRAVIA", cast: false, remote: true, adb: true))
        store.save(TailnetTV(ip: "192.168.1.42", name: "Home TV via Pi", cast: true))
        #expect(store.all.count == 2)

        let found = [DeviceInfo(ip: "192.168.1.42", name: "Living room", model: "TCL")]
        let merged = store.merged(into: found)
        #expect(merged.map(\.ip) == ["192.168.1.42", "100.88.7.6"])

        let bravia = DeviceInfo(ip: "100.88.7.6", name: "BRAVIA", model: "Tailscale")
        #expect(Tailnet.castBlockedReason(bravia, store: store) != nil)
        #expect(Tailnet.castBlockedReason(found[0], store: store) == nil)
        #expect(Tailnet.isTailnetAddress("192.168.1.42", store: store))   // added via a subnet route
        #expect(!Tailnet.isTailnetAddress("192.168.1.43", store: store))

        store.remove(ip: "100.88.7.6")
        #expect(store.all.map(\.ip) == ["192.168.1.42"])
    }

    @Test func test_acl_example_uses_login() {
        #expect(Tailnet.aclExample(login: "me@example.org").contains(#"["me@example.org"]"#))
        #expect(Tailnet.aclExample().contains("tag:tv:5555,6466,6467,8008,8009"))
    }

    @Test func test_resolve_prefers_tailnet_names() throws {
        let s = try TailscaleStatusParser.parse(Data(Self.sample.utf8))
        let r = try TailnetProbe.resolve("bravia-4k-vh2.tail1234.ts.net", status: s)
        #expect(r.ip == "100.88.7.6")
        #expect(r.host == "BRAVIA-4K-VH2")
        #expect(try TailnetProbe.resolve("100.99.9.9", status: s).host == "living-room")
    }
}

/// Canned `tailscale` output; records the arguments it was run with.
private final class StubRunner: ProcessRunning, @unchecked Sendable {
    var calls: [(exe: String, args: [String])] = []
    var stubbedOutput = ""
    var stubbedStderr = ""
    var stubbedExitCode: Int32 = 0

    func run(_ executable: String, _ args: [String], timeout: TimeInterval) throws -> ProcessResult {
        calls.append((executable, args))
        return ProcessResult(exitCode: stubbedExitCode, stdout: stubbedOutput, stderr: stubbedStderr)
    }
}
