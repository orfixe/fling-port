// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: what a TV announces about ADB on the network (adb's own mDNS), same
/// as the Windows / Linux build. `connect` is Android 11+ wireless debugging,
/// whose port changes on every reboot; `pairing` exists only while the TV
/// shows "Pair with code".
public struct ADBAnnounce: Equatable, Sendable {
    public var adb: Int?
    public var connect: Int?
    public var pairing: Int?
    public init(adb: Int? = nil, connect: Int? = nil, pairing: Int? = nil) {
        self.adb = adb; self.connect = connect; self.pairing = pairing
    }
}

/// What the TV's media session says is playing (`dumpsys media_session`).
public struct ADBNowPlaying: Equatable, Sendable {
    public enum State: String, Sendable { case playing, paused, buffering, idle }
    public var state: State
    public var title: String?
    public var artist: String?
    public var package: String?
    /// Position in seconds at `at`.
    public var elapsed: TimeInterval?
    public var at: Date
    /// Best-effort artwork and duration (YouTube search), filled in later.
    public var artwork: URL?
    public var duration: TimeInterval?

    public init(state: State, title: String? = nil, artist: String? = nil, package: String? = nil,
                elapsed: TimeInterval? = nil, at: Date = Date(), artwork: URL? = nil,
                duration: TimeInterval? = nil) {
        self.state = state; self.title = title; self.artist = artist; self.package = package
        self.elapsed = elapsed; self.at = at; self.artwork = artwork; self.duration = duration
    }

    public static let idle = ADBNowPlaying(state: .idle)

    public var isLive: Bool { state != .idle }

    /// Position now: a playing session keeps moving after it was read.
    public func position(now: Date = Date()) -> TimeInterval? {
        guard let elapsed else { return nil }
        let p = state == .playing ? elapsed + max(0, now.timeIntervalSince(at)) : elapsed
        if let duration, duration > 0 { return min(p, duration) }
        return p
    }
}

/// Pure parsers for adb output, tested against captured samples.
public enum ADBParse {

    static func isIPv4(_ s: String) -> Bool {
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 4 && parts.allSatisfy { p in
            !p.isEmpty && p.count <= 3 && p.allSatisfy(\.isASCII) && p.allSatisfy(\.isNumber)
                && (Int(p) ?? 999) <= 255
        }
    }

    /// `adb mdns services`:
    /// `adb-XYZ\t_adb-tls-connect._tcp\t192.168.1.50:37123` → ["192.168.1.50": connect 37123].
    public static func mdnsServices(_ out: String) -> [String: ADBAnnounce] {
        var map: [String: ADBAnnounce] = [:]
        for line in out.split(whereSeparator: \.isNewline) {
            let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
            guard parts.count >= 3 else { continue }
            var kind = parts[parts.count - 2]
            while kind.hasSuffix(".") { kind.removeLast() }
            let addr = parts[parts.count - 1]
            guard let colon = addr.lastIndex(of: ":") else { continue }
            let host = String(addr[..<colon])
            guard let port = Int(addr[addr.index(after: colon)...]), port > 0, port < 65536,
                  isIPv4(host) else { continue }
            var a = map[host] ?? ADBAnnounce()
            switch kind {
            case "_adb._tcp": a.adb = port
            case "_adb-tls-connect._tcp": a.connect = port
            case "_adb-tls-pairing._tcp": a.pairing = port
            default: continue
            }
            map[host] = a
        }
        return map
    }

    /// `adb devices`: serial → state (device | unauthorized | offline | …).
    public static func devices(_ out: String) -> [String: String] {
        var map: [String: String] = [:]
        for line in out.split(whereSeparator: \.isNewline) {
            let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
            guard parts.count >= 2, !line.hasPrefix("List of"), !line.hasPrefix("*") else { continue }
            map[parts[0]] = parts[1]
        }
        return map
    }

    /// The state of the first serial that belongs to `ip` (any port), with its port.
    public static func deviceState(_ out: String, ip: String) -> (state: String, port: Int?)? {
        for line in out.split(whereSeparator: \.isNewline) {
            let parts = line.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
            guard parts.count >= 2, parts[0].hasPrefix(ip + ":") else { continue }
            return (parts[1], Int(parts[0].dropFirst(ip.count + 1)))
        }
        return nil
    }

    public static func pairSucceeded(_ out: String) -> Bool {
        out.contains("Successfully paired")
    }

    /// `dumpsys media_session; echo __UP__; cat /proc/uptime` → what plays.
    /// Playing beats buffering beats paused; Cast's own stale mediashell
    /// session loses to another live app. nil when the output isn't that.
    public static func nowPlaying(_ out: String, at: Date = Date()) -> ADBNowPlaying? {
        guard let marker = out.range(of: "__UP__") else { return nil }
        let body = out[..<marker.lowerBound]
        let upText = out[marker.upperBound...].split(whereSeparator: { $0 == " " || $0.isNewline }).first
        let uptimeMs = (upText.flatMap { Double($0) } ?? 0) * 1000

        struct Session { var package: String; var st: Int?; var pos = 0.0; var speed = 1.0
                         var upd = 0.0; var title: String?; var artist: String? }
        var sessions: [Session] = []
        let stateRx = try! NSRegularExpression(
            pattern: #"state=(?:[A-Z_]+\()?(\d+)\)?, position=(-?\d+).*?speed=(-?[\d.]+), updated=(\d+)"#)
        for raw in body.split(whereSeparator: \.isNewline) {
            let l = raw.trimmingCharacters(in: .whitespaces)
            if l.hasPrefix("package=") {
                sessions.append(Session(package: String(l.dropFirst(8))))
                continue
            }
            guard !sessions.isEmpty else { continue }
            if l.hasPrefix("state=PlaybackState") {
                let ns = l as NSString
                if let m = stateRx.firstMatch(in: l, range: NSRange(location: 0, length: ns.length)) {
                    sessions[sessions.count - 1].st = Int(ns.substring(with: m.range(at: 1)))
                    sessions[sessions.count - 1].pos = Double(ns.substring(with: m.range(at: 2))) ?? 0
                    sessions[sessions.count - 1].speed = Double(ns.substring(with: m.range(at: 3))) ?? 1
                    sessions[sessions.count - 1].upd = Double(ns.substring(with: m.range(at: 4))) ?? 0
                }
            } else if l.hasPrefix("metadata:"), let d = l.range(of: "description=") {
                // description = "title, subtitle, description" — titles may contain commas
                let parts = l[d.upperBound...].split(separator: ",", omittingEmptySubsequences: false)
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                if parts.count >= 3 {
                    sessions[sessions.count - 1].title = parts.dropLast(2).joined(separator: ", ")
                    let artist = parts[parts.count - 2]
                    sessions[sessions.count - 1].artist = artist == "null" || artist.isEmpty ? nil : artist
                } else {
                    sessions[sessions.count - 1].title = parts.first
                }
            }
        }
        let rank: [Int: Int] = [3: 0, 6: 1, 2: 2]
        let live = sessions.filter { s in
            guard let st = s.st, rank[st] != nil, let t = s.title, !t.isEmpty, t != "null" else { return false }
            return true
        }
        let mediashell = "com.google.android.apps.mediashell"
        guard let x = live.min(by: { a, b in
            let ka = (rank[a.st!]!, a.package == mediashell ? 1 : 0)
            let kb = (rank[b.st!]!, b.package == mediashell ? 1 : 0)
            return ka < kb
        }) else { return ADBNowPlaying(state: .idle, at: at) }
        var pos = x.pos / 1000
        if x.st == 3 { pos += max(0, uptimeMs - x.upd) / 1000 * x.speed }
        let state: ADBNowPlaying.State = x.st == 3 ? .playing : x.st == 6 ? .buffering : .paused
        return ADBNowPlaying(state: state, title: x.title, artist: x.artist, package: x.package,
                             elapsed: max(0, pos), at: at)
    }

    /// adb install failure codes → what to do about them (same as the Windows build).
    public static let installErrors: [String: String] = [
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE":
            "The TV already has this app with a different signature — remove it on the TV and try again.",
        "INSTALL_FAILED_VERSION_DOWNGRADE": "The TV already has a newer version.",
        "INSTALL_FAILED_NO_MATCHING_ABIS":
            "This APK is built for another processor — the TV needs an armeabi-v7a / arm64 build.",
        "INSTALL_FAILED_OLDER_SDK": "This APK needs a newer Android than the TV has.",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "The TV is out of space.",
        "INSTALL_PARSE_FAILED_NO_CERTIFICATES": "The APK isn't signed.",
        "INSTALL_FAILED_INVALID_APK": "The file is damaged or isn't an APK.",
        "INSTALL_PARSE_FAILED_NOT_APK": "The file is damaged or isn't an APK.",
        "INSTALL_FAILED_MISSING_SPLIT": "The app comes in several APKs — choose them all at once.",
        "INSTALL_FAILED_USER_RESTRICTED": "The TV refused the install — confirm it on the TV screen.",
    ]

    /// nil on success, else a plain-words reason.
    public static func installFailure(exitCode: Int32, output: String) -> String? {
        if exitCode == 0 && output.contains("Success") { return nil }
        if let r = output.range(of: #"INSTALL_[A-Z_]+"#, options: .regularExpression) {
            let code = String(output[r])
            return installErrors[code] ?? code
        }
        return lastLine(output) ?? "The TV didn't answer."
    }

    public static func lastLine(_ s: String) -> String? {
        s.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
            .last(where: { !$0.isEmpty })
    }

    /// First YouTube search hit: artwork + duration for a title.
    public static func youtubeSearch(_ html: String) -> (image: URL, duration: TimeInterval?)? {
        guard let r = html.range(of: #""videoRenderer":\{"videoId":"([\w-]{11})""#,
                                 options: .regularExpression) else { return nil }
        let id = String(html[r].dropLast().suffix(11))
        guard let image = URL(string: "https://img.youtube.com/vi/\(id)/hqdefault.jpg") else { return nil }
        let seg = html[r.lowerBound...].prefix(6000)
        var duration: TimeInterval?
        if let d = seg.range(of: #""lengthText":\{"accessibility":\{"accessibilityData":\{"label":"[^"]*"\}\},"simpleText":"[\d:]+""#,
                             options: .regularExpression) {
            let text = seg[d].split(separator: "\"").last.map(String.init) ?? ""
            var sec = 0.0
            for p in text.split(separator: ":") { sec = sec * 60 + (Double(p) ?? 0) }
            if sec > 0 { duration = sec }
        }
        return (image, duration)
    }
}
