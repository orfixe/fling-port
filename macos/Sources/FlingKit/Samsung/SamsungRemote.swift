// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Where the remote's tokens live. The port keeps them in UserDefaults, not the
/// keychain (like the rest of the port: no keychain prompt per build).
public protocol SamsungTokenStore: AnyObject {
    func token(for key: String) -> String?
    func setToken(_ token: String, for key: String)
}

public final class DefaultsSamsungTokens: SamsungTokenStore {
    private let defaults: UserDefaults
    private let key = "samsungTokens"
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    public func token(for k: String) -> String? { (defaults.dictionary(forKey: key) as? [String: String])?[k] }
    public func setToken(_ token: String, for k: String) {
        var all = (defaults.dictionary(forKey: key) as? [String: String]) ?? [:]
        all[k] = token
        defaults.set(all, forKey: key)
    }
}

public final class MemorySamsungTokens: SamsungTokenStore {
    public var tokens: [String: String] = [:]
    public init() {}
    public func token(for key: String) -> String? { tokens[key] }
    public func setToken(_ token: String, for key: String) { tokens[key] = token }
}

/// Port: one WebSocket to a Samsung TV's remote-control channel (URLSession).
/// The TV speaks first: `ms.channel.connect` (in, with a token the first time)
/// or `ms.channel.unauthorized` (Deny). While it shows "Allow?" the socket is
/// open but silent — that's `.waiting`. wss://TV:8002 first; a 2016 set without
/// TLS gets ws://TV:8001.
@MainActor
public final class SamsungRemote {
    public enum State: Equatable, Sendable { case off, connecting, waiting, ready, denied, error }

    public private(set) var state: State = .off { didSet { if state != oldValue { onState?(state) } } }
    public private(set) var tv: SamsungTV?
    public private(set) var apps: [SamsungApp] = []
    public var onState: ((State) -> Void)?

    private let tokens: SamsungTokenStore
    private let tlsPort: Int
    private let plainPort: Int
    private let waitAfterOpen: Duration
    private let waitForAllow: Duration
    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var generation = 0
    private var opened = false
    private var triedPlain = false
    private var timer: Task<Void, Never>?
    private var textOpen = false

    public init(tokens: SamsungTokenStore = DefaultsSamsungTokens(),
                tlsPort: Int = SamsungProtocol.tlsPort, plainPort: Int = SamsungProtocol.plainPort,
                waitAfterOpen: Duration = .seconds(4), waitForAllow: Duration = .seconds(30)) {
        self.tokens = tokens
        self.tlsPort = tlsPort
        self.plainPort = plainPort
        self.waitAfterOpen = waitAfterOpen
        self.waitForAllow = waitForAllow
    }

    public func connect(_ tv: SamsungTV, plain: Bool = false) {
        teardown()
        self.tv = tv
        triedPlain = plain
        generation += 1
        let gen = generation
        guard let url = SamsungProtocol.remoteURL(host: tv.ip, token: tokens.token(for: tv.tokenKey),
                                                  plain: plain, tlsPort: tlsPort, plainPort: plainPort)
        else { state = .error; return }
        let delegate = Delegate(host: tv.ip) { [weak self] event in
            Task { @MainActor in self?.handle(event, gen: gen) }
        }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 10
        let session = URLSession(configuration: cfg, delegate: delegate, delegateQueue: nil)
        let task = session.webSocketTask(with: url)
        self.session = session
        self.task = task
        opened = false
        state = .connecting
        task.resume()
        receive(task, gen: gen)
        let wait = waitAfterOpen
        timer = Task { [weak self] in
            try? await Task.sleep(for: wait)
            guard !Task.isCancelled else { return }
            self?.waited(gen: gen)
        }
    }

    public func close() {
        teardown()
        state = .off
    }

    private func teardown() {
        generation += 1
        timer?.cancel(); timer = nil
        task?.cancel(with: .normalClosure, reason: nil); task = nil
        session?.invalidateAndCancel(); session = nil
        opened = false
        textOpen = false
    }

    // MARK: - commands (false when the remote isn't ready)

    @discardableResult public func key(_ code: String, cmd: String = "Click") -> Bool {
        if code == "KEY_ENTER", textOpen {      // typed text first lands, then Enter runs it
            textOpen = false
            send(SamsungProtocol.inputEndMessage)
        }
        return send(SamsungProtocol.keyMessage(code, cmd: cmd))
    }

    @discardableResult public func text(_ s: String) -> Bool {
        guard send(SamsungProtocol.textMessage(s)) else { return false }
        textOpen = true
        return true
    }

    @discardableResult public func launch(appId: String, type: Int = 2, meta: String = "") -> Bool {
        send(SamsungProtocol.launchMessage(appId: appId, appType: type, meta: meta))
    }

    @discardableResult private func send(_ text: String) -> Bool {
        guard state == .ready, let task else { return false }
        task.send(.string(text)) { _ in }
        return true
    }

    // MARK: - plumbing

    private func receive(_ task: URLSessionWebSocketTask, gen: Int) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self, gen == self.generation else { return }
                switch result {
                case .success(.string(let s)):
                    self.message(s)
                    self.receive(task, gen: gen)
                case .success(.data(let d)):
                    self.message(String(decoding: d, as: UTF8.self))
                    self.receive(task, gen: gen)
                case .success:
                    self.receive(task, gen: gen)
                case .failure:
                    self.dropped()
                }
            }
        }
    }

    private func handle(_ event: Delegate.Event, gen: Int) {
        guard gen == generation else { return }
        switch event {
        case .opened: opened = true
        case .closed, .failed: dropped()
        }
    }

    private func message(_ text: String) {
        switch SamsungProtocol.parse(text) {
        case .connected(let token):
            timer?.cancel(); timer = nil
            if let token, let tv { tokens.setToken(token, for: tv.tokenKey) }
            state = .ready
            send(SamsungProtocol.installedAppsMessage)
        case .unauthorized:
            timer?.cancel(); timer = nil
            state = .denied
            teardownKeepingState()
        case .timeOut:
            state = .error
            teardownKeepingState()
        case .installedApps(let list):
            if !list.isEmpty { apps = list }
        case .other:
            break
        }
    }

    private func teardownKeepingState() {
        let s = state
        teardown()
        state = s
    }

    /// The socket failed or closed: before the TV let us in, try ws://8001 once.
    private func dropped() {
        switch state {
        case .connecting:
            fallbackOrError()
        case .waiting:
            teardownKeepingState(); state = .error
        case .ready:
            teardown(); state = .off
        default:
            break
        }
    }

    private func fallbackOrError() {
        if !triedPlain, let tv {
            connect(tv, plain: true)
        } else {
            teardown(); state = .error
        }
    }

    private func waited(gen: Int) {
        guard gen == generation else { return }
        switch state {
        case .connecting:
            if opened {
                state = .waiting                    // the TV is showing "Allow?"
                let wait = waitForAllow
                timer = Task { [weak self] in
                    try? await Task.sleep(for: wait)
                    guard !Task.isCancelled, let self, gen == self.generation, self.state == .waiting else { return }
                    self.teardown(); self.state = .error
                }
            } else {
                fallbackOrError()
            }
        default:
            break
        }
    }

    /// Accepts the TV's self-signed certificate — for its own address only — and
    /// reports open / close / failure.
    private final class Delegate: NSObject, URLSessionWebSocketDelegate {
        enum Event { case opened, closed, failed }
        let host: String
        let report: (Event) -> Void
        init(host: String, report: @escaping (Event) -> Void) { self.host = host; self.report = report }

        func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
            if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
               challenge.protectionSpace.host == host, let trust = challenge.protectionSpace.serverTrust {
                completionHandler(.useCredential, URLCredential(trust: trust))
            } else {
                completionHandler(.performDefaultHandling, nil)
            }
        }

        func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                        didOpenWithProtocol protocol: String?) { report(.opened) }

        func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) { report(.closed) }

        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            if error != nil { report(.failed) }
        }
    }
}
