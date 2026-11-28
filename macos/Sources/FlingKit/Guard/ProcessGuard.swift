// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

public enum GuardMode: String, Sendable {
    /// a verified install: refuse, ask, stop
    case enforce
    /// a build from source (no verified install receipt): the same checks, only logged
    case reportOnly
}

/// What the guard tells the UI about.
public enum GuardEvent: Sendable {
    /// a program was not started; `reason` is one of `ExecutablePolicy.Reason`
    /// or "you did not allow it"
    case refused(executable: String, reason: String)
    /// a running program started by Fling was stopped
    case stopped(pid: Int32, executable: String, reason: StopReason)

    public enum StopReason: Sendable, Equatable {
        case cpu              // > 30 % of the machine for 90 s
        case unchecked(String) // a program started by one of Fling's programs that the rules refuse
    }
}

/// Thrown instead of starting a program the guard refuses.
public struct GuardRefusal: Error, LocalizedError, Equatable {
    public let executable: String
    public let reason: String
    public var errorDescription: String? {
        "Fling Port did not start \((executable as NSString).lastPathComponent): \(reason)"
    }
}

/// Port (macOS guard): the single gate every program Fling starts goes through
/// (`SystemProcessRunner` calls `authorize` before each launch; see ``GuardedProcess``).
public final class ProcessGuard: @unchecked Sendable {
    public static let shared = ProcessGuard(policy: ExecutablePolicy(trust: .shared))

    public static let declinedReason = "you did not allow it"
    public static let busyReason = "another question about a program is still open"

    public let policy: ExecutablePolicy
    public let log: GuardLog
    private let lock = NSLock()
    private let promptLock = NSLock()
    private var _mode: GuardMode = .reportOnly
    private var declined = Set<String>()
    private var trustedRoots = Set<Int32>()

    /// Asks the user whether to trust the files of a request (called on the launching
    /// thread; the UI hops to the main thread itself). nil = nobody to ask: refuse.
    public var askToTrust: ((ExecutablePolicy.TrustRequest) -> Bool)? {
        get { lock.lock(); defer { lock.unlock() }; return _ask }
        set { lock.lock(); _ask = newValue; lock.unlock() }
    }
    private var _ask: ((ExecutablePolicy.TrustRequest) -> Bool)?

    public var onEvent: ((GuardEvent) -> Void)? {
        get { lock.lock(); defer { lock.unlock() }; return _onEvent }
        set { lock.lock(); _onEvent = newValue; lock.unlock() }
    }
    private var _onEvent: ((GuardEvent) -> Void)?

    public var mode: GuardMode {
        get { lock.lock(); defer { lock.unlock() }; return _mode }
        set { lock.lock(); _mode = newValue; lock.unlock() }
    }

    public init(policy: ExecutablePolicy, log: GuardLog = .shared) {
        self.policy = policy
        self.log = log
    }

    /// Throws ``GuardRefusal`` when `executable` must not run. In report-only mode it
    /// only logs and never throws for policy reasons.
    public func authorize(_ executable: String) throws {
        let verdict = policy.evaluate(executable)
        switch verdict {
        case .allowed(let why):
            log.once("ok:\(why)", "info", "allowed \(executable) — \(why)")
        case .refused(let why):
            try refuse(executable, why, detail: why)
        case .needsTrust(let request):
            guard mode == .enforce else {
                log.once("ask:\(request.key)", "WARN",
                         "report-only: would ask to trust \(executable): \(describe(request))")
                return
            }
            try askOrRefuse(request)
        }
    }

    private func refuse(_ executable: String, _ reason: String, detail: String) throws {
        if mode == .enforce {
            log.alert("refused \(executable): \(detail)")
            onEvent?(.refused(executable: executable, reason: reason))
            throw GuardRefusal(executable: executable, reason: reason)
        }
        log.once("refuse:\(executable):\(detail)", "WARN", "report-only: would refuse \(executable): \(detail)")
    }

    private func askOrRefuse(_ first: ExecutablePolicy.TrustRequest) throws {
        // One question at a time; whoever waited re-checks (the answer may cover it).
        // The main thread never waits: the open question itself needs the main thread.
        if Thread.isMainThread {
            guard promptLock.try() else {
                throw GuardRefusal(executable: first.executable, reason: Self.busyReason)
            }
        } else {
            promptLock.lock()
        }
        defer { promptLock.unlock() }
        let request: ExecutablePolicy.TrustRequest
        switch policy.evaluate(first.executable) {
        case .allowed: return
        case .refused(let why): return try refuse(first.executable, why, detail: why)
        case .needsTrust(let r): request = r
        }
        lock.lock(); let wasDeclined = declined.contains(request.key); lock.unlock()
        if wasDeclined {
            throw GuardRefusal(executable: request.executable, reason: Self.declinedReason)
        }
        log.warn("asking to trust \(request.executable): \(describe(request))")
        if let ask = askToTrust, ask(request) {
            policy.trust.add(request.items, source: "asked")
            return
        }
        lock.lock(); declined.insert(request.key); lock.unlock()
        log.alert("refused \(request.executable): not allowed by the user")
        throw GuardRefusal(executable: request.executable, reason: Self.declinedReason)
    }

    private func describe(_ r: ExecutablePolicy.TrustRequest) -> String {
        r.items.map { "\($0.kind.rawValue) \($0.path) sha256=\($0.sha256)" +
            ($0.previousSHA256.map { " (was \($0))" } ?? " (new)") }.joined(separator: "; ")
    }

    // MARK: - descendants that may run anything (e.g. `brew install`)

    func trustDescendants(of pid: Int32) { lock.lock(); trustedRoots.insert(pid); lock.unlock() }
    func untrustDescendants(of pid: Int32) { lock.lock(); trustedRoots.remove(pid); lock.unlock() }
    var trustedDescendantRoots: Set<Int32> { lock.lock(); defer { lock.unlock() }; return trustedRoots }
}

/// The API for code that starts programs. Everything in FlingKit that uses
/// `SystemProcessRunner` is already covered; new code (adb, scrcpy, tailscale, brew…)
/// uses this.
public enum GuardedProcess {
    /// Throws ``GuardRefusal`` if the guard won't let `executable` run (may ask the user).
    public static func check(_ executable: String) throws {
        try ProcessGuard.shared.authorize(executable)
    }

    /// Checked, run to the end (or `timeout`), output captured — `SystemProcessRunner`.
    public static func run(_ executable: String, _ args: [String],
                           timeout: TimeInterval = ProcessTimeout.default) throws -> ProcessResult {
        try SystemProcessRunner().run(executable, args, timeout: timeout)
    }

    /// Checked and started, for long-running programs (scrcpy, a log stream…).
    /// `configure` sets pipes, environment, termination handler before the start.
    ///
    /// `trustDescendants`: programs *it* starts are not stopped by the guard's
    /// child check (still by the CPU watchdog). Only for installers such as
    /// `brew install`, which run compilers, git, curl, Ruby… from their own folders.
    @discardableResult
    public static func launch(_ executable: String, _ args: [String], trustDescendants: Bool = false,
                              configure: (Process) -> Void = { _ in }) throws -> Process {
        try check(executable)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: executable)
        p.arguments = args
        configure(p)
        if trustDescendants {
            let previous = p.terminationHandler
            p.terminationHandler = { proc in
                ProcessGuard.shared.untrustDescendants(of: proc.processIdentifier)
                previous?(proc)
            }
        }
        try p.run()
        if trustDescendants { ProcessGuard.shared.trustDescendants(of: p.processIdentifier) }
        return p
    }
}
