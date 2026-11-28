// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Port (desktop/fling_media.py): what can be cast from this Mac and how — local
/// formats, folders, m3u / pls playlists, subtitles next to a video, start times in links.
/// Everything here is pure or touches only the file system, so it is unit-tested.
public enum MediaKind: String, Sendable, Equatable {
    case video, audio, image, playlist, subtitles
}

/// One thing in the queue: a local file or a web link.
public struct MediaItem: Equatable, Sendable {
    public let src: String
    public let title: String?
    public let isWeb: Bool
    /// nil = look for subtitles next to the file; "off" = none; otherwise a path.
    public var subtitles: String?

    public init(src: String, title: String? = nil, isWeb: Bool = false, subtitles: String? = nil) {
        self.src = src; self.title = title; self.isWeb = isWeb; self.subtitles = subtitles
    }

    public var name: String {
        if let title, !title.isEmpty { return title }
        if isWeb {
            let u = URL(string: src)
            let last = u?.lastPathComponent.removingPercentEncoding ?? ""
            return (last.isEmpty || last == "/") ? (u?.host ?? src) : last
        }
        return MediaFiles.prettyTitle(src)
    }

    public var kind: MediaKind? { isWeb ? nil : MediaFiles.kind(ofPath: src) }
}

public enum MediaFiles {
    // The Default Media Receiver plays these as they are (Google Cast "supported media").
    public static let video: Set<String> = ["mp4", "m4v", "mov", "webm", "mkv"]
    public static let audio: Set<String> = ["mp3", "m4a", "aac", "flac", "wav", "ogg", "oga", "opus"]
    public static let image: Set<String> = ["jpg", "jpeg", "png", "gif", "bmp"]
    /// Re-encoded to JPEG first: Cast can't show them (ImageIO reads them all on macOS).
    public static let imageConvert: Set<String> = ["webp", "tif", "tiff", "heic", "heif", "avif"]
    public static let playlist: Set<String> = ["m3u", "m3u8", "pls"]
    public static let subs: Set<String> = ["srt", "vtt", "ass", "ssa"]

    public static let folderDepth = 3          // music: Artist / Album / CD1
    public static let folderLimit = 1000
    /// Remember the position only for things of 10+ minutes (films, episodes).
    public static let resumeMinDuration: TimeInterval = 600

    public static func ext(_ path: String) -> String {
        (path as NSString).pathExtension.lowercased()
    }

    public static func kind(ofPath path: String) -> MediaKind? {
        let e = ext(path)
        if video.contains(e) { return .video }
        if audio.contains(e) { return .audio }
        if image.contains(e) || imageConvert.contains(e) { return .image }
        if playlist.contains(e) { return .playlist }
        if subs.contains(e) { return .subtitles }
        return nil
    }

    public static func isWeb(_ s: String) -> Bool {
        guard let u = URL(string: s), let scheme = u.scheme?.lowercased(),
              scheme == "http" || scheme == "https", let host = u.host, !host.isEmpty else { return false }
        return true
    }

    public static func prettyTitle(_ path: String) -> String {
        let base = (path as NSString).lastPathComponent
        let stem = (base as NSString).deletingPathExtension
        return stem.isEmpty ? base : stem
    }

    /// "Episode 2" before "Episode 10" — the Finder's order.
    public static func naturalLess(_ a: String, _ b: String) -> Bool {
        a.localizedStandardCompare(b) == .orderedAscending
    }

    // MARK: - folders, playlists, dropped files

    /// Playable files in a folder and its subfolders: natural order, files before
    /// subfolders, hidden entries and symlinks skipped.
    public static func scanFolder(_ folder: String, depth: Int = folderDepth,
                                  limit: Int = folderLimit) -> [String] {
        var out: [String] = []
        func walk(_ dir: String, _ level: Int) {
            let fm = FileManager.default
            guard let names = try? fm.contentsOfDirectory(atPath: dir) else { return }
            var subdirs: [String] = []
            for name in names.sorted(by: naturalLess) {
                if out.count >= limit { return }
                if name.hasPrefix(".") { continue }
                let p = (dir as NSString).appendingPathComponent(name)
                guard let attrs = try? fm.attributesOfItem(atPath: p),
                      let type = attrs[.type] as? FileAttributeType else { continue }
                if type == .typeDirectory { subdirs.append(p) }
                else if type == .typeRegular,
                        let k = kind(ofPath: name), [.video, .audio, .image].contains(k) { out.append(p) }
            }
            if level < depth { for s in subdirs { walk(s, level + 1) } }
        }
        walk(folder, 0)
        return Array(out.prefix(limit))
    }

    /// m3u / m3u8 / pls text → items. Web links stay links; local entries resolve against
    /// `baseDir`; file://host/…, other schemes and missing files are dropped.
    public static func parsePlaylist(_ text: String, ext: String, baseDir: String,
                                     fileExists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) })
        -> [MediaItem] {
        var raw: [(String, String?)] = []
        let lines = text.components(separatedBy: .newlines)
        if ext == "pls" {
            var files: [Int: String] = [:], titles: [Int: String] = [:]
            let rx = try! NSRegularExpression(pattern: #"^\s*(File|Title)(\d+)\s*=\s*(.*\S)"#, options: [.caseInsensitive])
            for line in lines {
                let ns = line as NSString
                guard let m = rx.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)),
                      let n = Int(ns.substring(with: m.range(at: 2))) else { continue }
                let value = ns.substring(with: m.range(at: 3))
                if ns.substring(with: m.range(at: 1)).lowercased() == "file" { files[n] = value } else { titles[n] = value }
            }
            raw = files.keys.sorted().map { (files[$0]!, titles[$0]) }
        } else {
            var title: String?
            for line in lines.map({ $0.trimmingCharacters(in: .whitespaces) }) where !line.isEmpty {
                if line.hasPrefix("#") {
                    if line.hasPrefix("#EXTINF:"), let comma = line.firstIndex(of: ",") {
                        title = String(line[line.index(after: comma)...]).trimmingCharacters(in: .whitespaces)
                    }
                    continue
                }
                raw.append((line, title))
                title = nil
            }
        }
        var out: [MediaItem] = []
        for (srcRaw, title) in raw {
            let t = title?.nilIfEmpty
            if isWeb(srcRaw) { out.append(MediaItem(src: srcRaw, title: t, isWeb: true)); continue }
            var src = srcRaw
            if src.lowercased().hasPrefix("file:///") {
                src = URL(string: src)?.path ?? String(src.dropFirst(7))
            } else if src.range(of: #"^[a-zA-Z][a-zA-Z0-9+.-]*://"#, options: .regularExpression) != nil {
                continue                                      // file://host/…, smb://, rtsp://…
            }
            let path = ((src.hasPrefix("/") ? src : (baseDir as NSString).appendingPathComponent(src))
                        as NSString).standardizingPath
            guard fileExists(path), let k = kind(ofPath: path), [.video, .audio, .image].contains(k) else { continue }
            out.append(MediaItem(src: path, title: t))
        }
        return out
    }

    public static func readPlaylist(_ path: String) -> [MediaItem] {
        guard let data = FileManager.default.contents(atPath: path) else { return [] }
        return parsePlaylist(decodeText(data.prefix(4 * 1024 * 1024)), ext: ext(path),
                             baseDir: (path as NSString).deletingLastPathComponent)
    }

    /// Files, folders and playlists the user picked → one queue. Subtitle files picked
    /// together with exactly one video are attached to it.
    public static func expand(_ paths: [String]) -> (items: [MediaItem], skipped: [String]) {
        var items: [MediaItem] = [], skipped: [String] = [], subtitleFiles: [String] = []
        var isDir: ObjCBool = false
        for p in paths {
            if FileManager.default.fileExists(atPath: p, isDirectory: &isDir), isDir.boolValue {
                items += scanFolder(p).map { MediaItem(src: $0) }
                continue
            }
            switch kind(ofPath: p) {
            case .video?, .audio?, .image?: items.append(MediaItem(src: p))
            case .playlist?: items += readPlaylist(p)
            case .subtitles?: subtitleFiles.append(p)
            case nil: skipped.append(p)
            }
        }
        let videos = items.indices.filter { items[$0].kind == .video }
        if let sub = subtitleFiles.first, videos.count == 1 { items[videos[0]].subtitles = sub }
        else { skipped += subtitleFiles }
        return (items, skipped)
    }

    // MARK: - text

    /// Subtitles and playlists come in UTF-8, UTF-16 or (old Russian rips) Windows-1251.
    public static func decodeText(_ data: Data) -> String {
        let bytes = [UInt8](data.prefix(3))
        if bytes.starts(with: [0xEF, 0xBB, 0xBF]) { return String(decoding: data.dropFirst(3), as: UTF8.self) }
        if bytes.starts(with: [0xFF, 0xFE]) || bytes.starts(with: [0xFE, 0xFF]) {
            return String(data: data, encoding: .utf16) ?? ""
        }
        if let s = String(data: data, encoding: .utf8) { return s }
        if let cyr = String(data: data, encoding: .windowsCP1251) {
            let letters = cyr.unicodeScalars.filter { CharacterSet.letters.contains($0) }
            let cyrillic = letters.filter { (0x400...0x4FF).contains($0.value) }
            if !letters.isEmpty, Double(cyrillic.count) / Double(letters.count) > 0.3 { return cyr }
        }
        return String(data: data, encoding: .windowsCP1252) ?? String(decoding: data, as: UTF8.self)
    }

    // MARK: - subtitles

    /// A subtitle file for a video: next to it or in Subs/ / Subtitles/, named like the
    /// video (film.srt, film.ru.srt, film.English.vtt). The interface language wins.
    public static func findSubtitles(forVideo video: String, language: String) -> String? {
        let stem = prettyTitle(video).lowercased()
        let here = (video as NSString).deletingLastPathComponent
        let dirs = [here, "\(here)/Subs", "\(here)/Subtitles", "\(here)/subs", "\(here)/Subs/\(prettyTitle(video))"]
        var found: [String] = []
        for d in dirs {
            guard let names = try? FileManager.default.contentsOfDirectory(atPath: d) else { continue }
            for n in names where subs.contains(ext(n)) && (n.lowercased().hasPrefix(stem) || d.hasSuffix(prettyTitle(video))) {
                found.append((d as NSString).appendingPathComponent(n))
            }
        }
        guard !found.isEmpty else { return nil }
        let tags: Set<String> = language == "ru" ? ["ru", "rus", "russian", "рус"] : ["en", "eng", "english"]
        func score(_ p: String) -> (Int, Int, String) {
            let t = prettyTitle(p).lowercased()
            let rest = t.hasPrefix(stem) ? String(t.dropFirst(stem.count)) : t
            let words = Set(rest.components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty })
            let lang = !words.isDisjoint(with: tags) ? 0 : (words.isEmpty ? 1 : 2)
            return (lang, ["srt", "vtt"].contains(ext(p)) ? 0 : 1, p)
        }
        return found.min { score($0) < score($1) }
    }

    public static func toVTT(_ text: String, ext: String) -> String {
        var t = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        if t.hasPrefix("\u{FEFF}") { t.removeFirst() }
        if ext == "ass" || ext == "ssa" { return assToVTT(t) }
        if ext == "vtt" || t.hasPrefix("WEBVTT") { return t }
        let fixed = t.replacingOccurrences(of: #"(\d{1,2}:\d{2}:\d{2}),(\d{3})"#, with: "$1.$2",
                                           options: .regularExpression)
        return "WEBVTT\n\n" + fixed.drop(while: { $0.isWhitespace })
    }

    /// Plain text of ASS / SSA dialogue lines (styles and effects are dropped).
    static func assToVTT(_ text: String) -> String {
        var fmt: [String]?
        var cues: [(String, String, String)] = []
        func ts(_ v: String) -> String? {
            let p = v.trimmingCharacters(in: .whitespaces).split(separator: ":")
            guard p.count == 3, let h = Int(p[0]), let m = Int(p[1]) else { return nil }
            let sp = p[2].split(separator: ".", omittingEmptySubsequences: false)
            guard let s = Int(sp[0]) else { return nil }
            let cs = (sp.count > 1 ? String(sp[1]) : "") + "00"
            return String(format: "%02d:%02d:%02d.%@0", h, m, s, String(cs.prefix(2)))
        }
        for line in text.components(separatedBy: "\n") {
            if line.hasPrefix("Format:"), fmt == nil, line.contains("Start") {
                fmt = line.dropFirst(7).split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            } else if line.hasPrefix("Dialogue:"), let fmt {
                let parts = line.dropFirst(9).split(separator: ",", maxSplits: fmt.count - 1, omittingEmptySubsequences: false)
                guard parts.count >= fmt.count else { continue }
                var d: [String: String] = [:]
                for (k, v) in zip(fmt, parts) { d[k] = String(v) }
                let body = (d["text"] ?? "")
                    .replacingOccurrences(of: #"\{[^}]*\}"#, with: "", options: .regularExpression)
                    .replacingOccurrences(of: "\\N", with: "\n").replacingOccurrences(of: "\\n", with: "\n")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                guard !body.isEmpty, let a = d["start"].flatMap(ts), let b = d["end"].flatMap(ts) else { continue }
                cues.append((a, b, body))
            }
        }
        cues.sort { ($0.0, $0.1) < ($1.0, $1.1) }
        return "WEBVTT\n\n" + cues.map { "\($0.0) --> \($0.1)\n\($0.2)" }.joined(separator: "\n\n") + "\n"
    }

    /// Scratch folder for converted subtitles and photos; old files are swept on launch.
    public static var tmpDir: String {
        let d = (NSTemporaryDirectory() as NSString).appendingPathComponent("fling-cast")
        try? FileManager.default.createDirectory(atPath: d, withIntermediateDirectories: true)
        return d
    }

    private static func tmpName(_ src: String, _ suffix: String) -> String {
        let a = try? FileManager.default.attributesOfItem(atPath: src)
        let sig = "\(src)|\((a?[.size] as? NSNumber)?.intValue ?? 0)|\((a?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0)"
        var h: UInt64 = 0xcbf29ce484222325                      // FNV-1a: stable across launches
        for b in sig.utf8 { h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return (tmpDir as NSString).appendingPathComponent(String(format: "%016llx", h) + suffix)
    }

    /// → a UTF-8 WebVTT copy. catt reads SRT as UTF-8 / Latin-1 only (Cyrillic
    /// Windows-1251 turned into mojibake) and can't read ASS at all.
    public static func prepareSubtitles(_ path: String) -> String? {
        let out = tmpName(path, ".vtt")
        if FileManager.default.fileExists(atPath: out) { return out }
        guard let data = FileManager.default.contents(atPath: path) else { return nil }
        let vtt = toVTT(decodeText(data), ext: ext(path))
        do { try vtt.write(toFile: out, atomically: true, encoding: .utf8) } catch { return nil }
        return out
    }

    /// A picture the TV can show: as it is, or re-encoded to JPEG (webp / tiff / heic,
    /// or bigger than 4K, which older Chromecasts refuse). nil if it can't be decoded.
    public static func prepareImage(_ path: String, maxSide: Int = 3840) -> String? {
        let e = ext(path)
        guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else {
            return image.contains(e) ? path : nil
        }
        let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        let w = (props?[kCGImagePropertyPixelWidth] as? Int) ?? 0
        let h = (props?[kCGImagePropertyPixelHeight] as? Int) ?? 0
        let big = max(w, h) > maxSide
        if image.contains(e) && !big { return path }
        let out = tmpName(path, ".jpg")
        if FileManager.default.fileExists(atPath: out) { return out }
        let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                     kCGImageSourceCreateThumbnailWithTransform: true,
                                     kCGImageSourceThumbnailMaxPixelSize: min(maxSide, max(w, h, 1))]
        guard let img = CGImageSourceCreateThumbnailAtIndex(source, 0, opts as CFDictionary),
              let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: out) as CFURL,
                                                         UTType.jpeg.identifier as CFString, 1, nil)
        else { return image.contains(e) ? path : nil }
        CGImageDestinationAddImage(dest, img, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
        return CGImageDestinationFinalize(dest) ? out : (image.contains(e) ? path : nil)
    }

    public static func cleanTmp(maxAge: TimeInterval = 2 * 86400) {
        let fm = FileManager.default
        for n in (try? fm.contentsOfDirectory(atPath: tmpDir)) ?? [] {
            let p = (tmpDir as NSString).appendingPathComponent(n)
            if let d = (try? fm.attributesOfItem(atPath: p))?[.modificationDate] as? Date,
               Date().timeIntervalSince(d) > maxAge { try? fm.removeItem(atPath: p) }
        }
    }

    // MARK: - timestamps, resume keys

    /// "90", "90s", "1m30s", "1h2m3s", "1:30", "01:02:03" → seconds.
    public static func parseTime(_ raw: String?) -> TimeInterval? {
        let v = (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        guard !v.isEmpty else { return nil }
        if v.range(of: #"^\d+(\.\d+)?s?$"#, options: .regularExpression) != nil {
            return Double(v.hasSuffix("s") ? String(v.dropLast()) : v)
        }
        func groups(_ pattern: String) -> [String?]? {
            let rx = try! NSRegularExpression(pattern: pattern)
            let ns = v as NSString
            guard let m = rx.firstMatch(in: v, range: NSRange(location: 0, length: ns.length)) else { return nil }
            return (1..<m.numberOfRanges).map { m.range(at: $0).location == NSNotFound ? nil : ns.substring(with: m.range(at: $0)) }
        }
        if let g = groups(#"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$"#), g.contains(where: { $0 != nil }) {
            return Double(g[0].flatMap { Int($0) } ?? 0) * 3600 + Double(g[1].flatMap { Int($0) } ?? 0) * 60
                + (g[2].flatMap { Double($0) } ?? 0)
        }
        if let g = groups(#"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$"#) {
            return Double(g[0].flatMap { Int($0) } ?? 0) * 3600 + Double(Int(g[1]!)!) * 60 + Double(g[2]!)!
        }
        return nil
    }

    /// Start position written into a link: ?t=, ?start=, ?time_continue=, #t= (media fragment).
    public static func urlStart(_ url: String) -> TimeInterval? {
        guard let c = URLComponents(string: url) else { return nil }
        for k in ["t", "start", "time_continue"] {
            if let v = c.queryItems?.first(where: { $0.name == k })?.value, let t = parseTime(v), t > 0 { return t }
        }
        if let frag = c.fragment {
            for part in frag.split(separator: "&") where part.hasPrefix("t=") {
                return parseTime(String(part.dropFirst(2).split(separator: ",").first ?? ""))
            }
        }
        return nil
    }

    /// One key per thing, whatever link points at it: YouTube by video id, files by path,
    /// other links without the fragment and time parameters.
    public static func resumeKey(_ src: String) -> String {
        guard isWeb(src) else { return (src as NSString).standardizingPath }
        if let id = URLClassifier.youTubeVideoID(src) { return "yt:" + id }
        guard var c = URLComponents(string: src) else { return src }
        c.fragment = nil
        let kept = c.queryItems?.filter { !["t", "start", "time_continue"].contains($0.name) }
        c.queryItems = (kept?.isEmpty ?? true) ? nil : kept
        return c.string ?? src
    }
}
