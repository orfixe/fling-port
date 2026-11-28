// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Darwin

/// Port: finding Samsung TVs (SSDP, then the TV's /api/v2/) and waking them
/// (Wake-on-LAN), as desktop/fling_samsung.py does.
///
/// SSDP uses a plain UDP socket rather than Network.framework: an M-SEARCH is
/// answered by unicast to the port it was sent from, and NWConnectionGroup
/// sends from the group port (1900, often shared with other apps) — a socket on
/// an ephemeral port gets every answer and needs no multicast entitlement.
public enum SamsungDiscovery {

    public struct Found: Sendable {
        public var tvs: [SamsungTV]
        /// The DLNA description URL each renderer gave in its SSDP answer, by IP.
        public var dlna: [String: String]
    }

    /// SSDP for Samsung remotes and DLNA renderers, then /api/v2/ on each answer.
    /// `target` defaults to the SSDP multicast group (tests point it at a simulator).
    public static func discover(timeout: TimeInterval = 3,
                                target: (host: String, port: UInt16) = ("239.255.255.250", 1900),
                                apiPort: Int = SamsungProtocol.plainPort) async -> Found {
        let answers = await Task.detached(priority: .userInitiated) {
            ssdp(timeout: timeout, target: target)
        }.value
        var dlna: [String: String] = [:]
        var ips: [String] = []
        for (ip, a) in answers {
            if a.isRenderer, let loc = a.location { dlna[ip] = loc }
            if a.maybeSamsung, !ips.contains(ip) { ips.append(ip) }
        }
        var tvs: [SamsungTV] = []
        await withTaskGroup(of: SamsungTV?.self) { group in
            for ip in ips { group.addTask { await info(ip: ip, port: apiPort) } }
            for await tv in group { if let tv { tvs.append(tv) } }
        }
        return Found(tvs: tvs.sorted { $0.name < $1.name }, dlna: dlna)
    }

    /// The TV's own description, nil when nothing Tizen answers at `ip`.
    public static func info(ip: String, port: Int = SamsungProtocol.plainPort,
                            timeout: TimeInterval = 2.5) async -> SamsungTV? {
        guard let url = URL(string: "http://\(ip):\(port)/api/v2/") else { return nil }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = timeout
        cfg.timeoutIntervalForResource = timeout
        let session = URLSession(configuration: cfg)
        defer { session.finishTasksAndInvalidate() }
        guard let (data, resp) = try? await session.data(from: url),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return SamsungProtocol.deviceInfo(json: data, ip: ip)
    }

    /// Blocking: sends the M-SEARCHes, collects answers until `timeout`, by sender IP.
    static func ssdp(timeout: TimeInterval, target: (host: String, port: UInt16))
        -> [(String, SamsungProtocol.SSDPAnswer)] {
        let fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard fd >= 0 else { return [] }
        defer { close(fd) }
        var ttl: UInt8 = 2
        setsockopt(fd, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, socklen_t(MemoryLayout<UInt8>.size))
        var tv = timeval(tv_sec: 0, tv_usec: 300_000)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        var dest = sockaddr_in()
        dest.sin_family = sa_family_t(AF_INET)
        dest.sin_port = target.port.bigEndian
        inet_pton(AF_INET, target.host, &dest.sin_addr)
        for st in SamsungProtocol.searchTargets {
            let msg = Array(SamsungProtocol.mSearch(st).utf8)
            for _ in 0..<2 {
                withUnsafePointer(to: &dest) { p in
                    p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        _ = sendto(fd, msg, msg.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                    }
                }
            }
        }

        var out: [(String, SamsungProtocol.SSDPAnswer)] = []
        var buf = [UInt8](repeating: 0, count: 65_535)
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            var from = sockaddr_in()
            var len = socklen_t(MemoryLayout<sockaddr_in>.size)
            let n = withUnsafeMutablePointer(to: &from) { p in
                p.withMemoryRebound(to: sockaddr.self, capacity: 1) { recvfrom(fd, &buf, buf.count, 0, $0, &len) }
            }
            if n <= 0 {
                if errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR { continue }
                break
            }
            var addr = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            inet_ntop(AF_INET, &from.sin_addr, &addr, socklen_t(INET_ADDRSTRLEN))
            let ip = String(cString: addr)
            if let a = SamsungProtocol.parseSSDP(String(decoding: buf[0..<n], as: UTF8.self)) {
                out.append((ip, a))
            }
        }
        return out
    }

    /// Wake-on-LAN to the broadcast address and the TV's /24, ports 9 and 7.
    /// Needs "Power On with Mobile" on the TV. False when `mac` isn't a MAC.
    @discardableResult
    public static func wake(mac: String, ip: String?) -> Bool {
        guard let packet = SamsungProtocol.magicPacket(mac: mac) else { return false }
        var targets = ["255.255.255.255"]
        if let ip, ip.split(separator: ".").count == 4 {
            targets.append(ip.split(separator: ".").prefix(3).joined(separator: ".") + ".255")
        }
        let fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &on, socklen_t(MemoryLayout<Int32>.size))
        let bytes = [UInt8](packet)
        for _ in 0..<3 {
            for t in targets {
                for port: UInt16 in [9, 7] {
                    var dest = sockaddr_in()
                    dest.sin_family = sa_family_t(AF_INET)
                    dest.sin_port = port.bigEndian
                    inet_pton(AF_INET, t, &dest.sin_addr)
                    withUnsafePointer(to: &dest) { p in
                        p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                            _ = sendto(fd, bytes, bytes.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                        }
                    }
                }
            }
        }
        return true
    }
}
