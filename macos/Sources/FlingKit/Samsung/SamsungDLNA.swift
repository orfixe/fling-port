// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port: links and media to a Samsung TV's DLNA renderer (UPnP AVTransport), the
/// same requests as desktop/fling_samsung.py. The TV fetches and plays the URL
/// itself; the Mac only tells it what to play.
public enum SamsungDLNA {
    public static let avTransport = "urn:schemas-upnp-org:service:AVTransport:1"
    static let dlnaFeatures = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

    public enum Failure: Error, Equatable {
        case noRenderer
        case soap(status: Int, body: String)
    }

    // MARK: - pure pieces

    /// The AVTransport control URL in a renderer description, resolved against `base`.
    public static func controlURL(description: Data, base: URL) -> URL? {
        let p = ServiceParser()
        let parser = XMLParser(data: description)
        parser.delegate = p
        parser.parse()
        guard let path = p.services.first(where: { $0.type.contains("AVTransport") })?.control,
              !path.isEmpty else { return nil }
        return URL(string: path, relativeTo: base)?.absoluteURL
    }

    public static func envelope(action: String, args: String = "") -> String {
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        + "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        + "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>"
        + "<u:\(action) xmlns:u=\"\(avTransport)\"><InstanceID>0</InstanceID>\(args)</u:\(action)>"
        + "</s:Body></s:Envelope>"
    }

    public static func escape(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
         .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
         .replacingOccurrences(of: "'", with: "&#39;")
    }

    public static func didl(url: String, title: String, mime: String) -> String {
        let cls = mime.hasPrefix("image/") ? "object.item.imageItem.photo"
            : mime.hasPrefix("audio/") ? "object.item.audioItem.musicTrack" : "object.item.videoItem"
        return "<DIDL-Lite xmlns=\"urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/\" "
            + "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" "
            + "xmlns:upnp=\"urn:schemas-upnp-org:metadata-1-0/upnp/\">"
            + "<item id=\"0\" parentID=\"-1\" restricted=\"1\"><dc:title>\(escape(title))</dc:title>"
            + "<upnp:class>\(cls)</upnp:class>"
            + "<res protocolInfo=\"http-get:*:\(mime):\(dlnaFeatures)\">\(escape(url))</res>"
            + "</item></DIDL-Lite>"
    }

    public static func setURIArgs(url: String, title: String, mime: String) -> String {
        "<CurrentURI>\(escape(url))</CurrentURI>"
        + "<CurrentURIMetaData>\(escape(didl(url: url, title: title, mime: mime)))</CurrentURIMetaData>"
    }

    public static func mime(for url: String) -> String {
        let ext = (URL(string: url)?.pathExtension ?? "").lowercased()
        let table: [String: String] = [
            "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "mkv": "video/x-matroska",
            "webm": "video/webm", "avi": "video/x-msvideo", "ts": "video/mp2t",
            "m3u8": "application/vnd.apple.mpegurl", "mpd": "application/dash+xml",
            "mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac",
            "wav": "audio/wav", "ogg": "audio/ogg", "oga": "audio/ogg",
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
            "webp": "image/webp", "bmp": "image/bmp",
        ]
        return table[ext] ?? "video/mp4"
    }

    // MARK: - requests

    /// The control URL: from the SSDP location when there is one, else Samsung's usual `:9197/dmr`.
    public static func findControl(ip: String, location: String?, port: Int = SamsungProtocol.dlnaPort,
                                   session: URLSession = .shared) async -> URL? {
        let candidates = [location, "http://\(ip):\(port)/dmr"].compactMap { $0 }.compactMap(URL.init(string:))
        for loc in candidates {
            var req = URLRequest(url: loc, timeoutInterval: 3)
            req.httpMethod = "GET"
            guard let (data, resp) = try? await session.data(for: req),
                  (resp as? HTTPURLResponse)?.statusCode == 200,
                  let control = controlURL(description: data, base: loc) else { continue }
            return control
        }
        return nil
    }

    @discardableResult
    public static func soap(_ control: URL, action: String, args: String = "",
                            session: URLSession = .shared) async throws -> String {
        var req = URLRequest(url: control, timeoutInterval: 8)
        req.httpMethod = "POST"
        req.setValue("text/xml; charset=\"utf-8\"", forHTTPHeaderField: "Content-Type")
        req.setValue("\"\(avTransport)#\(action)\"", forHTTPHeaderField: "SOAPAction")
        req.httpBody = Data(envelope(action: action, args: args).utf8)
        let (data, resp) = try await session.data(for: req)
        let body = String(decoding: data, as: UTF8.self)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw Failure.soap(status: status, body: body) }
        return body
    }

    /// Stop whatever plays, hand the TV the URL, play.
    public static func play(_ control: URL, url: String, title: String, mime: String,
                            session: URLSession = .shared) async throws {
        _ = try? await soap(control, action: "Stop", session: session)
        try await soap(control, action: "SetAVTransportURI",
                       args: setURIArgs(url: url, title: title, mime: mime), session: session)
        try await soap(control, action: "Play", args: "<Speed>1</Speed>", session: session)
    }
}

/// `<service><serviceType/><controlURL/></service>` from a UPnP device description.
private final class ServiceParser: NSObject, XMLParserDelegate {
    struct Service { var type = ""; var control = "" }
    var services: [Service] = []
    private var current: Service?
    private var text = ""

    func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
                qualifiedName: String?, attributes: [String: String] = [:]) {
        let local = name.split(separator: ":").last.map(String.init) ?? name
        if local == "service" { current = Service() }
        text = ""
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) { text += string }

    func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?,
                qualifiedName: String?) {
        let local = name.split(separator: ":").last.map(String.init) ?? name
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        switch local {
        case "serviceType": current?.type = value
        case "controlURL": current?.control = value
        case "service": if let c = current { services.append(c) }; current = nil
        default: break
        }
        text = ""
    }
}
