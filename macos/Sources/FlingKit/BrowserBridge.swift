// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Network

/// Port (desktop/fling_bridge.py): the local bridge between the Fling browser extension
/// (Chrome, Edge, Brave, Opera, Vivaldi, Firefox, Safari) and the app — the same protocol as
/// on Windows / Linux, so the one extension works with every build.
///
/// A tiny HTTP server on 127.0.0.1 only. It accepts browser *extensions* and local tools,
/// never web pages:
/// * the Host header must be 127.0.0.1:<port> / localhost:<port> / [::1]:<port> (DNS rebinding);
/// * a request with an Origin must come from an extension origin;
/// * POST needs `X-Fling: 1` and a JSON object body, so a web page can't send even a
///   "simple" cross-site request without a preflight — and web-origin preflights are refused;
/// * optionally (`allowedIDs`) only the listed extension IDs are accepted.
public enum BridgeHTTP {
    public static let defaultPort: UInt16 = 47613
    public static let maxBody = 64 * 1024
    static let extensionSchemes = ["chrome-extension://", "moz-extension://", "safari-web-extension://", "extension://"]

    public struct Request: Equatable, Sendable {
        public let method: String
        public let path: String
        /// Lower-cased names.
        public let headers: [String: String]
        public let body: Data
    }

    public enum Parsed: Equatable, Sendable {
        case incomplete
        case bad(code: Int)
        case request(Request)
    }

    /// Bytes read so far → a whole request, more needed, or junk.
    public static func parse(_ data: Data) -> Parsed {
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else {
            return data.count > 16 * 1024 ? .bad(code: 431) : .incomplete
        }
        let head = String(decoding: data[data.startIndex..<end.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        let first = lines.removeFirst().split(separator: " ")
        guard first.count >= 2 else { return .bad(code: 400) }
        var headers: [String: String] = [:]
        for l in lines {
            guard let colon = l.firstIndex(of: ":") else { continue }
            headers[l[..<colon].trimmingCharacters(in: .whitespaces).lowercased()] =
                l[l.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length: Int
        if let raw = headers["content-length"] {
            guard let n = Int(raw), n >= 0 else { return .bad(code: 400) }
            length = n
        } else { length = 0 }
        if length > maxBody { return .bad(code: 413) }
        let bodyStart = end.upperBound
        guard data.count - (bodyStart - data.startIndex) >= length else { return .incomplete }
        let body = data[bodyStart..<(bodyStart + length)]
        let path = String(first[1]).split(separator: "?", maxSplits: 1).first.map(String.init) ?? "/"
        return .request(Request(method: String(first[0]).uppercased(), path: path, headers: headers, body: Data(body)))
    }

    public static func hostOK(_ host: String?, port: UInt16) -> Bool {
        let h = (host ?? "").lowercased()
        return ["127.0.0.1:\(port)", "localhost:\(port)", "[::1]:\(port)"].contains(h)
    }

    /// (accepted, the origin to echo in CORS headers).
    public static func originOK(_ origin: String?, allowedIDs: Set<String>) -> (Bool, String?) {
        guard let origin, origin != "null", !origin.isEmpty else { return (true, nil) }   // curl, local tools
        guard extensionSchemes.contains(where: origin.hasPrefix) else { return (false, origin) }
        if !allowedIDs.isEmpty {
            let id = origin.components(separatedBy: "://")[1].split(separator: "/").first.map { $0.lowercased() } ?? ""
            if !allowedIDs.contains(id) { return (false, origin) }
        }
        return (true, origin)
    }

    /// What to do with a request, before the app is asked.
    public enum Route: Equatable {
        case reply(code: Int, json: [String: String]?, origin: String?)
        case preflight(origin: String)
        /// Ask the app: `path` with the JSON body (empty for GET).
        case dispatch(path: String, body: Data, origin: String?)
    }

    public static func route(_ r: Request, port: UInt16, allowedIDs: Set<String> = []) -> Route {
        guard hostOK(r.headers["host"], port: port) else {
            return .reply(code: 403, json: r.method == "OPTIONS" ? nil : ["ok": "false", "error": "bad host"], origin: nil)
        }
        let (ok, origin) = originOK(r.headers["origin"], allowedIDs: allowedIDs)
        switch r.method {
        case "OPTIONS":
            guard ok, let origin else { return .reply(code: 403, json: nil, origin: nil) }
            return .preflight(origin: origin)
        case "GET":
            guard ok else { return .reply(code: 403, json: ["error": "origin not allowed"], origin: nil) }
            guard ["/v1/ping", "/v1/status"].contains(r.path) else {
                return .reply(code: 404, json: ["error": "not found"], origin: origin)
            }
            return .dispatch(path: r.path, body: Data(), origin: origin)
        case "POST":
            guard ok else { return .reply(code: 403, json: ["error": "origin not allowed"], origin: nil) }
            guard r.headers["x-fling"] == "1" else {
                return .reply(code: 403, json: ["error": "missing X-Fling header"], origin: origin)
            }
            let obj = r.body.isEmpty ? [:] : (try? JSONSerialization.jsonObject(with: r.body)) as? [String: Any]
            guard obj != nil else { return .reply(code: 400, json: ["error": "JSON object expected"], origin: origin) }
            guard ["/v1/action", "/v1/tab"].contains(r.path) else {
                return .reply(code: 404, json: ["error": "not found"], origin: origin)
            }
            return .dispatch(path: r.path, body: r.body, origin: origin)
        default:
            return .reply(code: 405, json: ["error": "method not allowed"], origin: origin)
        }
    }

    static let reasons = [200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                          405: "Method Not Allowed", 413: "Payload Too Large", 431: "Request Header Fields Too Large",
                          500: "Internal Server Error", 503: "Service Unavailable"]

    public static func response(code: Int, body: Data?, origin: String?, preflight: Bool = false) -> Data {
        var h = "HTTP/1.1 \(code) \(reasons[code] ?? "Status")\r\nServer: Fling\r\nConnection: close\r\n"
        if let origin {
            h += "Access-Control-Allow-Origin: \(origin)\r\nVary: Origin\r\n"
        }
        if preflight {
            h += "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
            h += "Access-Control-Allow-Headers: Content-Type, X-Fling\r\n"
            h += "Access-Control-Allow-Private-Network: true\r\nAccess-Control-Max-Age: 600\r\n"
        }
        h += "Cache-Control: no-store\r\n"
        if body != nil { h += "Content-Type: application/json; charset=utf-8\r\n" }
        h += "Content-Length: \(body?.count ?? 0)\r\n\r\n"
        return Data(h.utf8) + (body ?? Data())
    }

    public static func json(_ obj: Any) -> Data {
        (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])) ?? Data("{}".utf8)
    }

    static func errorJSON(_ fields: [String: String]?) -> Data? {
        guard let fields else { return nil }
        var o: [String: Any] = ["ok": false]
        if let e = fields["error"] { o["error"] = e }
        return json(o)
    }
}

/// The server. `handler(path, body)` runs on the main actor and returns the JSON reply.
public final class BrowserBridge: @unchecked Sendable {
    public typealias Handler = @MainActor (String, [String: Any]) async throws -> [String: Any]

    public let port: UInt16
    private let allowedIDs: Set<String>
    private let handler: Handler
    private var listener: NWListener?
    private let queue = DispatchQueue(label: "fling-bridge")
    public private(set) var error: String?

    public init(port: UInt16 = BridgeHTTP.defaultPort, allowedIDs: Set<String> = [], handler: @escaping Handler) {
        self.port = port
        self.allowedIDs = Set(allowedIDs.map { $0.lowercased() })
        self.handler = handler
    }

    @discardableResult
    public func start() -> Bool {
        do {
            let params = NWParameters.tcp
            params.allowLocalEndpointReuse = false
            params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
            let l = try NWListener(using: params)
            l.newConnectionHandler = { [weak self] c in self?.accept(c) }
            l.stateUpdateHandler = { [weak self] st in
                if case .failed(let e) = st { self?.error = "port \(self?.port ?? 0): \(e)"; NSLog("bridge: %@", "\(e)") }
            }
            l.start(queue: queue)
            listener = l
            NSLog("bridge: listening on 127.0.0.1:%d", Int(port))
            return true
        } catch {
            self.error = "port \(port): \(error)"
            return false
        }
    }

    public func stop() { listener?.cancel(); listener = nil }

    private func accept(_ c: NWConnection) {
        c.start(queue: queue)
        receive(c, Data())
        // a client that never finishes its request is dropped
        queue.asyncAfter(deadline: .now() + 10) { if c.state != .cancelled { c.cancel() } }
    }

    private func receive(_ c: NWConnection, _ got: Data) {
        c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, done, err in
            guard let self else { c.cancel(); return }
            var buf = got
            if let data { buf.append(data) }
            switch BridgeHTTP.parse(buf) {
            case .incomplete:
                if done || err != nil { c.cancel() } else { self.receive(c, buf) }
            case .bad(let code):
                self.send(c, BridgeHTTP.response(code: code, body: BridgeHTTP.errorJSON(["error": "bad request"]), origin: nil))
            case .request(let r):
                self.handle(c, r)
            }
        }
    }

    private func handle(_ c: NWConnection, _ r: BridgeHTTP.Request) {
        switch BridgeHTTP.route(r, port: port, allowedIDs: allowedIDs) {
        case .reply(let code, let fields, let origin):
            send(c, BridgeHTTP.response(code: code, body: BridgeHTTP.errorJSON(fields), origin: origin))
        case .preflight(let origin):
            send(c, BridgeHTTP.response(code: 204, body: nil, origin: origin, preflight: true))
        case .dispatch(let path, let body, let origin):
            let obj = (body.isEmpty ? [:] : (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]) ?? [:]
            let handler = self.handler
            let box = ReplyOnce { [weak self] data in self?.send(c, data) }
            Task { @MainActor in
                do {
                    let result = try await handler(path, obj)
                    box.reply(BridgeHTTP.response(code: 200, body: BridgeHTTP.json(result), origin: origin))
                } catch {
                    box.reply(BridgeHTTP.response(code: 500, body: BridgeHTTP.json(["ok": false, "error": "\(error)"]),
                                                  origin: origin))
                }
            }
            queue.asyncAfter(deadline: .now() + 4) {
                box.reply(BridgeHTTP.response(code: 503, body: BridgeHTTP.json(["ok": false, "error": "Fling is busy"]),
                                              origin: origin))
            }
        }
    }

    private func send(_ c: NWConnection, _ data: Data) {
        c.send(content: data, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { _ in
            c.cancel()
        })
    }

    /// The handler's answer or the busy timeout, whichever comes first.
    private final class ReplyOnce: @unchecked Sendable {
        private let lock = NSLock()
        private var sent = false
        private let out: (Data) -> Void
        init(_ out: @escaping (Data) -> Void) { self.out = out }
        func reply(_ d: Data) {
            lock.lock(); let first = !sent; sent = true; lock.unlock()
            if first { out(d) }
        }
    }
}

/// Errors the bridge handler throws for a bad request (reported to the extension).
public struct BridgeError: Error, CustomStringConvertible {
    public let description: String
    public init(_ d: String) { description = d }
}
