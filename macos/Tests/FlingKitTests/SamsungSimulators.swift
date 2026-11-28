// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Network

/// Local stand-ins for a Samsung TV, on 127.0.0.1 only: its remote WebSocket,
/// its HTTP side (/api/v2/ and the DLNA renderer) and its SSDP answers. No real
/// TV is ever contacted by the tests.
final class Locked<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T
    init(_ v: T) { value = v }
    func get() -> T { lock.lock(); defer { lock.unlock() }; return value }
    func mutate(_ f: (inout T) -> Void) { lock.lock(); f(&value); lock.unlock() }
}

private func waitForPort(_ listener: NWListener) throws -> UInt16 {
    let ready = DispatchSemaphore(value: 0)
    listener.stateUpdateHandler = { st in
        if case .ready = st { ready.signal() }
        if case .failed = st { ready.signal() }
    }
    listener.start(queue: .global())
    _ = ready.wait(timeout: .now() + 5)
    guard let p = listener.port?.rawValue, p != 0 else { throw NSError(domain: "sim", code: 1) }
    return p
}

private func localParams(_ base: NWParameters) -> NWParameters {
    base.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
    base.allowLocalEndpointReuse = true
    return base
}

/// The TV's remote channel. `mode` decides what it says when a client connects.
final class FakeSamsungRemoteServer: @unchecked Sendable {
    enum Mode { case allow(token: String?), deny, silent }
    let port: UInt16
    let received = Locked<[String]>([])
    let connections = Locked<[NWConnection]>([])
    private let listener: NWListener
    private let mode: Mode

    init(mode: Mode) throws {
        self.mode = mode
        let params = localParams(.tcp)
        let ws = NWProtocolWebSocket.Options()
        ws.autoReplyPing = true
        params.defaultProtocolStack.applicationProtocols.insert(ws, at: 0)
        listener = try NWListener(using: params)
        listener.newConnectionHandler = { _ in }
        let sim = Locked<FakeSamsungRemoteServer?>(nil)
        listener.newConnectionHandler = { conn in sim.get()?.accept(conn) }
        port = try waitForPort(listener)
        sim.mutate { $0 = self }
    }

    private func accept(_ conn: NWConnection) {
        connections.mutate { $0.append(conn) }
        conn.stateUpdateHandler = { [weak self] st in
            guard let self, case .ready = st else { return }
            switch self.mode {
            case .allow(let token):
                var data: [String: Any] = ["clients": [], "id": "x"]
                if let token { data["token"] = token }
                self.send(["event": "ms.channel.connect", "data": data], on: conn)
            case .deny:
                self.send(["event": "ms.channel.unauthorized"], on: conn)
            case .silent:
                break              // the TV is showing "Allow?"
            }
        }
        conn.start(queue: .global())
        receive(conn)
    }

    /// The user pressed Allow on the TV (for `.silent`).
    func allowAll(token: String) {
        for c in connections.get() {
            send(["event": "ms.channel.connect", "data": ["token": token]], on: c)
        }
    }

    func send(_ obj: [String: Any], on conn: NWConnection) {
        let data = try! JSONSerialization.data(withJSONObject: obj)
        let meta = NWProtocolWebSocket.Metadata(opcode: .text)
        let ctx = NWConnection.ContentContext(identifier: "text", metadata: [meta])
        conn.send(content: data, contentContext: ctx, isComplete: true, completion: .contentProcessed { _ in })
    }

    private func receive(_ conn: NWConnection) {
        conn.receiveMessage { [weak self] data, _, _, error in
            guard let self, error == nil else { return }
            if let data, !data.isEmpty {
                let text = String(decoding: data, as: UTF8.self)
                self.received.mutate { $0.append(text) }
                if text.contains("ed.installedApp.get") {
                    self.send(["event": "ed.installedApp.get", "data": ["data": [
                        ["appId": "3201504001965", "name": "Tubi", "app_type": 2]]]], on: conn)
                }
            }
            self.receive(conn)
        }
    }

    func stop() {
        listener.cancel()
        connections.get().forEach { $0.cancel() }
    }
}

/// A tiny HTTP/1.1 server: one request per connection, answered by `handler`.
final class FakeHTTPServer: @unchecked Sendable {
    struct Request { let method: String; let path: String; let headers: [String: String]; let body: String }
    let port: UInt16
    let requests = Locked<[Request]>([])
    private let listener: NWListener
    private let handler: @Sendable (Request) -> (Int, String)

    init(handler: @escaping @Sendable (Request) -> (Int, String)) throws {
        self.handler = handler
        listener = try NWListener(using: localParams(.tcp))
        let sim = Locked<FakeHTTPServer?>(nil)
        listener.newConnectionHandler = { conn in
            conn.start(queue: .global())
            sim.get()?.read(conn, buffer: Data())
        }
        port = try waitForPort(listener)
        sim.mutate { $0 = self }
    }

    private func read(_ conn: NWConnection, buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, done, error in
            guard let self else { return }
            var buf = buffer
            if let data { buf.append(data) }
            if let req = Self.parse(buf) {
                self.requests.mutate { $0.append(req) }
                let (status, body) = self.handler(req)
                let bytes = Data(body.utf8)
                let head = "HTTP/1.1 \(status) X\r\nContent-Type: text/xml\r\nContent-Length: \(bytes.count)\r\nConnection: close\r\n\r\n"
                conn.send(content: Data(head.utf8) + bytes, completion: .contentProcessed { _ in conn.cancel() })
            } else if error == nil && !done {
                self.read(conn, buffer: buf)
            } else {
                conn.cancel()
            }
        }
    }

    static func parse(_ data: Data) -> Request? {
        let text = String(decoding: data, as: UTF8.self)
        guard let split = text.range(of: "\r\n\r\n") else { return nil }
        let lines = text[..<split.lowerBound].components(separatedBy: "\r\n")
        let first = lines[0].split(separator: " ")
        guard first.count >= 2 else { return nil }
        var headers: [String: String] = [:]
        for l in lines.dropFirst() {
            if let c = l.firstIndex(of: ":") {
                headers[l[..<c].lowercased()] = l[l.index(after: c)...].trimmingCharacters(in: .whitespaces)
            }
        }
        let body = String(text[split.upperBound...])
        if let n = headers["content-length"].flatMap(Int.init), body.utf8.count < n { return nil }
        return Request(method: String(first[0]), path: String(first[1]), headers: headers, body: body)
    }

    func stop() { listener.cancel() }
}

/// Answers M-SEARCHes like a Samsung TV would, pointing at `location`.
final class FakeSSDPResponder: @unchecked Sendable {
    let port: UInt16
    let searches = Locked<Int>(0)
    private let listener: NWListener

    init(location: String) throws {
        listener = try NWListener(using: localParams(.udp))
        let count = searches
        listener.newConnectionHandler = { conn in
            conn.start(queue: .global())
            func loop() {
                conn.receiveMessage { data, _, _, error in
                    guard error == nil, let data else { return }
                    if String(decoding: data, as: UTF8.self).hasPrefix("M-SEARCH") {
                        count.mutate { $0 += 1 }
                        let st = String(decoding: data, as: UTF8.self).contains("RemoteControlReceiver")
                            ? "urn:samsung.com:device:RemoteControlReceiver:1"
                            : "urn:schemas-upnp-org:device:MediaRenderer:1"
                        let reply = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nST: \(st)\r\n"
                            + "LOCATION: \(location)\r\nSERVER: SHP, UPnP/1.0, Samsung UPnP SDK/1.0\r\n\r\n"
                        conn.send(content: Data(reply.utf8), completion: .contentProcessed { _ in })
                    }
                    loop()
                }
            }
            loop()
        }
        port = try waitForPort(listener)
    }

    func stop() { listener.cancel() }
}

enum FakeTVDocs {
    static func apiV2(name: String = "[TV] Living room &amp; kitchen", mac: String = "AA:BB:CC:DD:EE:FF") -> String {
        """
        {"device":{"name":"\(name)","modelName":"QE55Q80TAU","wifiMac":"\(mac)","PowerState":"on",
        "TokenAuthSupport":"true","OS":"Tizen"},"name":"\(name)","type":"Samsung SmartTV"}
        """
    }

    static let description = """
        <?xml version="1.0"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0"><device>
        <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
        <serviceList>
        <service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <controlURL>/upnp/control/RenderingControl1</controlURL></service>
        <service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>/upnp/control/AVTransport1</controlURL></service>
        </serviceList></device></root>
        """
}
