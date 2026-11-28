// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: TV connection robustness — TLS refusal retries for a paired TV, the
// pairing mark, dropped presses, stopping a wait, device memory, the switch offer.
// swift-testing (not XCTest) and self-contained, so it also runs with the
// Command Line Tools alone, which ship Testing.framework but no XCTest.
import Foundation
import Network
import Testing
@testable import FlingKit

// MARK: - helpers

/// In-memory transport: scripted server frames, records what the client sent.
private final class PortFakeTransport: ATVTransporting, @unchecked Sendable {
    private let lock = NSLock()
    private var pending: [Data] = []
    private var waiter: CheckedContinuation<Data, Error>?
    private var closed = false
    private var sent: [Data] = []
    var startError: Error?

    func start() async throws { if let startError { throw startError } }

    func send(_ message: Data) async throws {
        lock.lock(); sent.append(message); lock.unlock()
    }

    func receiveMessage() async throws -> Data {
        try await withCheckedThrowingContinuation { cont in
            lock.lock()
            if !pending.isEmpty { let m = pending.removeFirst(); lock.unlock(); cont.resume(returning: m); return }
            if closed { lock.unlock(); cont.resume(throwing: ATVError.connectionClosed); return }
            waiter = cont
            lock.unlock()
        }
    }

    func push(_ m: Data) {
        lock.lock()
        if let w = waiter { waiter = nil; lock.unlock(); w.resume(returning: m); return }
        pending.append(m)
        lock.unlock()
    }

    func cancel() {
        lock.lock(); closed = true; let w = waiter; waiter = nil; lock.unlock()
        w?.resume(throwing: ATVError.connectionClosed)
    }

    var sentMessages: [Data] { lock.lock(); defer { lock.unlock() }; return sent }

    /// RemoteConfigure → RemoteSetActive → RemoteStart(on), as a TV opens a session.
    func pushHandshake() {
        var w = ProtoWriter()
        w.message(1) { c in c.varint(1, 0x26F) }
        push(w.data)
        push(ATVHex.data("1200")!)
        push(ATVHex.data("c202020801")!)
    }
}

/// Hands out transports; `refuse(n, host)` decides whether the n-th attempt
/// (0-based, all hosts) fails the TLS handshake.
private final class Transports: @unchecked Sendable {
    private let lock = NSLock()
    private var made: [(host: String, t: PortFakeTransport)] = []
    private let refuse: @Sendable (Int, String) -> Bool

    init(refuse: @escaping @Sendable (Int, String) -> Bool) { self.refuse = refuse }

    func make(_ host: String) -> PortFakeTransport {
        let t = PortFakeTransport()
        lock.lock()
        let n = made.count
        made.append((host, t))
        lock.unlock()
        if refuse(n, host) { t.startError = ATVError.tlsHandshakeFailed("-9806") } else { t.pushHandshake() }
        return t
    }

    var all: [(host: String, t: PortFakeTransport)] { lock.lock(); defer { lock.unlock() }; return made }
    var sent: [Data] { all.flatMap { $0.t.sentMessages } }
}

private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Bool
    init(_ v: Bool) { value = v }
    var on: Bool {
        get { lock.lock(); defer { lock.unlock() }; return value }
        set { lock.lock(); value = newValue; lock.unlock() }
    }
}

private func makeStore(paired: [String]) throws -> ATVCertificateStore {
    let dir = FileManager.default.temporaryDirectory
        .appendingPathComponent("fling-port-retry-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    // The identity files only need to exist: the fake transport bypasses TLS.
    try Data().write(to: dir.appendingPathComponent("atv-identity.p12"))
    try Data().write(to: dir.appendingPathComponent("atv-client-cert.der"))
    let s = ATVCertificateStore(directory: dir)
    for h in paired { s.recordPairedHost(h) }
    return s
}

private func makeFacade(_ store: ATVCertificateStore, _ transports: Transports,
                        waits: [TimeInterval] = [0.05, 0.05, 0.05],
                        memory: TimeInterval = 60) -> AndroidTVRemote {
    let remote = ATVRemoteClient(store: store, transportFactory: { host, _ in transports.make(host) })
    return AndroidTVRemote(store: store, remote: remote, retryWaits: waits, refusalMemory: memory)
}

private func waitUntil(_ timeout: TimeInterval = 2, _ cond: () async -> Bool) async -> Bool {
    let start = Date()
    while Date().timeIntervalSince(start) < timeout {
        if await cond() { return true }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
    return await cond()
}

private func key(_ code: Int32) -> Data { ATVRemoteMessage.keyInject(keyCode: code) }

// MARK: - TLS refusal retries

@Suite struct PortRetryTests {

    @Test func pairedTVRefusingTwiceIsRetriedAndStaysPaired() async throws {
        let s = try makeStore(paired: ["tv"])
        let t = Transports { n, _ in n < 2 }
        let atv = makeFacade(s, t)
        let waited = try await atv.connectTracked(host: "tv")
        #expect(waited, "the connect had to wait for a retry")
        #expect(t.all.count == 3)
        #expect(atv.hasPairing(host: "tv"))
    }

    @Test func tvThatReallyForgotDropsTheMarkAfterThreeRetries() async throws {
        let s = try makeStore(paired: ["tv"])
        let t = Transports { _, _ in true }
        let atv = makeFacade(s, t)
        await #expect(throws: ATVError.tlsHandshakeFailed("-9806")) { try await atv.connect(host: "tv") }
        #expect(t.all.count == 4, "first try + a retry after each of the three waits")
        #expect(!atv.hasPairing(host: "tv"), "the panel must show Pair with TV")
        #expect(TVConnectionText.message(for: ATVError.tlsHandshakeFailed("x")) == TVConnectionText.forgotten)

        // No knocking again right away: it refused this Mac in the last minute.
        #expect(await atv.mayTry(host: "tv") == false)
        await #expect(throws: ATVError.notPaired) { try await atv.pressKey(ATVKeyCode.dpadUp, host: "tv") }
        #expect(t.all.count == 4, "no new attempt")
    }

    @Test func noRetryStormForATVThatRefusedAMomentAgo() async throws {
        let s = try makeStore(paired: ["tv"])
        let t = Transports { _, _ in true }
        let atv = makeFacade(s, t)
        _ = try? await atv.connect(host: "tv")
        #expect(t.all.count == 4)
        s.recordPairedHost("tv")                      // the mark is back (e.g. set by hand)
        _ = try? await atv.connect(host: "tv")
        #expect(t.all.count == 5, "one attempt, not a second round of retries")
    }

    @Test func droppedMarkGetsOneAttemptLaterAndSuccessRestoresIt() async throws {
        let s = try makeStore(paired: ["tv"])
        let refusing = Flag(true)
        let t = Transports { _, _ in refusing.on }
        let atv = makeFacade(s, t, memory: 0)        // the refusal is forgotten at once
        _ = try? await atv.connect(host: "tv")
        #expect(!atv.hasPairing(host: "tv"))
        refusing.on = false
        #expect(await atv.mayTry(host: "tv"))
        try await atv.pressKey(ATVKeyCode.dpadUp, host: "tv")
        #expect(atv.hasPairing(host: "tv"), "the TV still trusts this Mac: mark restored")
        #expect(t.sent.contains(key(ATVKeyCode.dpadUp)))
    }

    @Test func pressDuringTheWaitIsDroppedNotDeliveredLate() async throws {
        let s = try makeStore(paired: ["tv"])
        let t = Transports { n, _ in n == 0 }
        let atv = makeFacade(s, t, waits: [0.6])
        let first = Task { try await atv.pressKey(ATVKeyCode.dpadUp, host: "tv") }
        #expect(await waitUntil { await atv.waitingHost == "tv" })
        await #expect(throws: ATVError.reconnecting) { try await atv.pressKey(ATVKeyCode.dpadDown, host: "tv") }
        #expect(TVConnectionText.message(for: ATVError.reconnecting) == TVConnectionText.reconnecting)
        #expect(TVConnectionText.isProgressNote(TVConnectionText.reconnecting), "grey, not red")
        #expect(!TVConnectionText.isProgressNote(TVConnectionText.forgotten), "errors are red")

        // The press that started the connect is stale once the retry worked.
        await #expect(throws: ATVError.commandDropped) { try await first.value }
        #expect(TVConnectionText.message(for: ATVError.commandDropped) == nil, "said nothing")
        #expect(!t.sent.contains(key(ATVKeyCode.dpadUp)))
        #expect(!t.sent.contains(key(ATVKeyCode.dpadDown)))

        // The session is up now: the next press goes through at once.
        try await atv.pressKey(ATVKeyCode.dpadLeft, host: "tv")
        #expect(t.sent.contains(key(ATVKeyCode.dpadLeft)))
    }

    @Test func stopReconnectingEndsTheWaitQuietlyAndKeepsThePairing() async throws {
        let s = try makeStore(paired: ["tv"])
        let t = Transports { _, _ in true }
        let atv = makeFacade(s, t, waits: [5, 5, 5])
        let started = Date()
        let connect = Task { try await atv.connect(host: "tv") }
        #expect(await waitUntil { await atv.waitingHost == "tv" })
        await atv.stopReconnecting()
        await #expect(throws: ATVError.superseded) { _ = try await connect.value }
        #expect(Date().timeIntervalSince(started) < 3)
        #expect(atv.hasPairing(host: "tv"), "no verdict from a stopped wait")
        #expect(await atv.waitingHost == nil)
        #expect(TVConnectionText.message(for: ATVError.superseded) == nil)
    }

    @Test func switchingTVStopsTheWaitForTheOldOne() async throws {
        let s = try makeStore(paired: ["a", "b"])
        let t = Transports { _, host in host == "a" }
        let atv = makeFacade(s, t, waits: [5, 5, 5])
        let started = Date()
        let old = Task { try await atv.connect(host: "a") }
        #expect(await waitUntil { await atv.waitingHost == "a" })
        try await atv.pressKey(ATVKeyCode.home, host: "b")
        #expect(Date().timeIntervalSince(started) < 3)
        await #expect(throws: ATVError.superseded) { _ = try await old.value }
        #expect(atv.hasPairing(host: "a"))
        #expect(t.all.last?.host == "b")
        #expect(t.all.last?.t.sentMessages.contains(key(ATVKeyCode.home)) == true)
    }

    @Test func neverPairedTVIsNotKnockedOn() async throws {
        let s = try makeStore(paired: [])
        let t = Transports { _, _ in true }
        let atv = makeFacade(s, t)
        await #expect(throws: ATVError.notPaired) { try await atv.connect(host: "tv") }
        #expect(t.all.isEmpty, "nothing sent to a TV that was never paired")
        #expect(TVConnectionText.message(for: ATVError.notPaired) == TVConnectionText.notPaired)
    }

    @Test func plainWordsInsteadOfRawErrors() {
        let unreachable = TVConnectionText.message(for: ATVError.connectionFailed("POSIXErrorCode(rawValue: 64): Host is down"))
        #expect(unreachable == TVConnectionText.unreachable)
        #expect(unreachable?.contains("POSIX") == false)
        #expect(TVConnectionText.message(for: ATVError.certificateFailure("openssl exited 1")) == TVConnectionText.certificate)
        #expect(TVConnectionText.message(for: ATVError.connectionTimeout) == TVConnectionText.timeout)
    }
}

// MARK: - the chosen TV and the known TVs

@Suite struct PortDeviceMemoryTests {
    let a = DeviceInfo(ip: "10.0.0.2", name: "Living room", model: "Google TV")
    let b = DeviceInfo(ip: "10.0.0.3", name: "Bedroom", model: "Chromecast")

    @Test func failedSearchKeepsTheKnownTVsAndTheChoice() {
        let r = DeviceMemory.merge(found: nil, known: [a, b], selected: b)
        #expect(r.devices == [a, b])
        #expect(r.selected == b)
        #expect(DeviceMemory.merge(found: [], known: [a, b], selected: b).selected == b)
    }

    @Test func chosenTVMissingFromTheSearchStaysChosenAndListed() {
        let r = DeviceMemory.merge(found: [b], known: [a, b], selected: a)
        #expect(r.selected == a, "not swapped for the other TV")
        #expect(Set(r.devices) == [a, b])
    }

    @Test func chosenTVUnderANewAddressIsFollowedOnlyWhenUnambiguous() {
        let moved = DeviceInfo(ip: "10.0.0.9", name: a.name, model: a.model)
        #expect(DeviceMemory.merge(found: [moved, b], known: [a, b], selected: a).selected == moved)
        let twin = DeviceInfo(ip: "10.0.0.10", name: a.name, model: a.model)
        #expect(DeviceMemory.merge(found: [moved, twin], known: [a], selected: a).selected == a,
                "two TVs with that name: keep the chosen one")
    }

    @Test func firstSearchPicksTheFirstTV() {
        #expect(DeviceMemory.merge(found: [b, a], known: [], selected: nil).selected == b)
    }

    @Test func choiceSurvivesARestart() throws {
        let suite = "fling-port-tests-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        DeviceMemory(defaults: defaults).save(selected: b, known: [a, b])
        let loaded = DeviceMemory(defaults: defaults).load()
        #expect(loaded.selected == b)
        #expect(loaded.known == [a, b])
    }
}

// MARK: - offline TV, one-click switch

@Suite struct PortSwitchAdvisorTests {
    let a = DeviceInfo(ip: "1", name: "Living room", model: "")
    let b = DeviceInfo(ip: "2", name: "Bedroom", model: "")
    let c = DeviceInfo(ip: "3", name: "Attic", model: "")

    @Test func offersAnotherTVAfterTwoOfflineChecks() {
        var adv = SwitchAdvisor()
        let online = ["1": false, "2": true]
        #expect(adv.suggestion(current: a, devices: [a, b], online: online) == nil, "one miss isn't enough")
        #expect(adv.suggestion(current: a, devices: [a, b], online: online) == b)
        #expect(adv.suggestion(current: a, devices: [a, b], online: ["1": true, "2": true]) == nil,
                "the TV is back: no offer")
    }

    @Test func prefersPairedThenByNameAndNeverAnOfflineOne() {
        let online = ["1": false, "2": true, "3": true]
        var byName = SwitchAdvisor()
        _ = byName.suggestion(current: a, devices: [a, b, c], online: online)
        #expect(byName.suggestion(current: a, devices: [a, b, c], online: online) == c)
        var paired = SwitchAdvisor()
        _ = paired.suggestion(current: a, devices: [a, b, c], online: online, preferred: ["2"])
        #expect(paired.suggestion(current: a, devices: [a, b, c], online: online, preferred: ["2"]) == b)
        var none = SwitchAdvisor()
        let allAway = ["1": false, "2": false]
        _ = none.suggestion(current: a, devices: [a, b], online: allAway)
        #expect(none.suggestion(current: a, devices: [a, b], online: allAway) == nil)
    }
}

@Suite struct PortReachabilityTests {
    @Test func openPortAnswersAndClosedPortDoesNot() async throws {
        let listener = try NWListener(using: .tcp, on: .any)
        listener.newConnectionHandler = { $0.cancel() }
        listener.start(queue: .global())
        defer { listener.cancel() }
        #expect(await waitUntil(3) { listener.port?.rawValue ?? 0 != 0 })
        let port = try #require(listener.port?.rawValue)
        #expect(await TVReachability.isReachable(host: "127.0.0.1", ports: [port], timeout: 2))
        listener.cancel()
        try await Task.sleep(nanoseconds: 300_000_000)
        #expect(await TVReachability.isReachable(host: "127.0.0.1", ports: [port], timeout: 1) == false)
    }
}
