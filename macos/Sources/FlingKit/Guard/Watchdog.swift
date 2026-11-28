// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Darwin
import Foundation

/// One process, told apart from a later one that reuses its pid.
public struct ProcessKey: Hashable, Sendable {
    public let pid: Int32
    public let start: UInt64   // start time, µs since 1970
    public init(pid: Int32, start: UInt64) { self.pid = pid; self.start = start }
}

/// The CPU rule, without timers or processes (so it can be tested): a process that keeps
/// more than `threshold` of the whole machine (all cores) busy for `window` seconds
/// is to be stopped. Fling's own pid is never returned.
public struct CPUWatchdogLogic {
    public let threshold: Double
    public let window: TimeInterval
    public let selfPID: Int32

    struct Track { var cpuNanos: UInt64; var time: TimeInterval; var overSince: TimeInterval? }
    private(set) var tracks: [ProcessKey: Track] = [:]

    public init(threshold: Double = 0.30, window: TimeInterval = 90, selfPID: Int32 = getpid()) {
        self.threshold = threshold
        self.window = window
        self.selfPID = selfPID
    }

    /// `samples`: total CPU time (user + system, ns) of each live process at `now`.
    /// Returns the processes over the limit for the whole window.
    public mutating func observe(_ samples: [ProcessKey: UInt64], now: TimeInterval, cpuCount: Int) -> [ProcessKey] {
        var stop: [ProcessKey] = []
        let cores = Double(max(cpuCount, 1))
        for (key, cpu) in samples where key.pid != selfPID {
            guard var t = tracks[key] else {
                tracks[key] = Track(cpuNanos: cpu, time: now, overSince: nil)
                continue
            }
            let dt = now - t.time
            guard dt > 0 else { continue }
            let used = Double(cpu &- min(cpu, t.cpuNanos)) / 1e9
            let share = used / (dt * cores)
            if share > threshold {
                let since = t.overSince ?? t.time
                if now - since >= window {
                    stop.append(key)
                    t.overSince = nil
                } else {
                    t.overSince = since
                }
            } else {
                t.overSince = nil
            }
            t.cpuNanos = cpu
            t.time = now
            tracks[key] = t
        }
        for key in tracks.keys where samples[key] == nil { tracks[key] = nil }
        return stop.sorted { $0.pid < $1.pid }
    }
}

/// libproc: Fling's process tree, paths and CPU times.
public enum ProcessTree {
    public struct Node: Sendable { public let key: ProcessKey; public let parent: Int32 }

    public static func children(of pid: Int32) -> [Int32] {
        var buf = [Int32](repeating: 0, count: 256)
        let n = buf.withUnsafeMutableBytes { proc_listchildpids(pid, $0.baseAddress, Int32($0.count)) }
        guard n > 0 else { return [] }
        // returns a count of pids on macOS (bytes on some older systems): clamp either way
        let count = min(Int(n), buf.count)
        return buf.prefix(count).filter { $0 > 0 }
    }

    /// Every descendant of `root` (not `root` itself), parents before children.
    public static func descendants(of root: Int32 = getpid()) -> [Node] {
        var out: [Node] = []
        var queue = [root]
        var seen: Set<Int32> = [root]
        while !queue.isEmpty {
            let p = queue.removeFirst()
            for c in children(of: p) where seen.insert(c).inserted {
                guard let start = startTime(c) else { continue }
                out.append(Node(key: ProcessKey(pid: c, start: start), parent: p))
                queue.append(c)
            }
        }
        return out
    }

    public static func path(of pid: Int32) -> String? {
        var buf = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
        let n = proc_pidpath(pid, &buf, UInt32(buf.count))
        return n > 0 ? String(cString: buf) : nil
    }

    public static func startTime(_ pid: Int32) -> UInt64? {
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
        return info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec
    }

    private static let timebase: (numer: UInt64, denom: UInt64) = {
        var tb = mach_timebase_info_data_t()
        mach_timebase_info(&tb)
        return (UInt64(max(tb.numer, 1)), UInt64(max(tb.denom, 1)))
    }()

    /// User + system CPU time in nanoseconds (rusage reports Mach time units).
    public static func cpuNanos(_ pid: Int32) -> UInt64? {
        var usage = rusage_info_v2()
        let r = withUnsafeMutablePointer(to: &usage) { ptr in
            ptr.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) {
                proc_pid_rusage(pid, RUSAGE_INFO_V2, $0)
            }
        }
        guard r == 0 else { return nil }
        let ticks = usage.ri_user_time &+ usage.ri_system_time
        return ticks.multipliedReportingOverflow(by: timebase.numer).partialValue / timebase.denom
    }
}

/// Port (macOS guard): every 5 s looks at all processes below Fling —
/// - stops one that the rules refuse (programs started *by* Fling's programs, which no
///   launch check saw), unless it descends from a launch with `trustDescendants`;
/// - stops one that keeps more than 30 % of the machine busy for 90 s.
/// In report-only mode both are only logged. Fling itself is never stopped.
public final class GuardWatchdog: @unchecked Sendable {
    public let processGuard: ProcessGuard
    public let interval: TimeInterval
    private var logic: CPUWatchdogLogic
    private var checked: [ProcessKey: ExecutablePolicy.Verdict] = [:]
    private var timer: DispatchSourceTimer?
    private let queue = DispatchQueue(label: "fling.guard.watchdog", qos: .utility)

    public init(guard g: ProcessGuard = .shared, interval: TimeInterval = 5,
                threshold: Double = 0.30, window: TimeInterval = 90) {
        processGuard = g
        self.interval = interval
        logic = CPUWatchdogLogic(threshold: threshold, window: window)
    }

    public func start() {
        queue.async { [self] in
            guard timer == nil else { return }
            let t = DispatchSource.makeTimerSource(queue: queue)
            t.schedule(deadline: .now() + interval, repeating: interval, leeway: .seconds(1))
            t.setEventHandler { [weak self] in self?.tick() }
            t.resume()
            timer = t
        }
    }

    public func stop() { queue.async { [self] in timer?.cancel(); timer = nil } }

    /// One look at the tree (also callable from tests).
    public func tick() {
        let nodes = ProcessTree.descendants()
        let enforce = processGuard.mode == .enforce
        let trustedRoots = processGuard.trustedDescendantRoots
        var parentOf: [Int32: Int32] = [:]
        for n in nodes { parentOf[n.key.pid] = n.parent }
        let me = getpid()
        func underTrustedRoot(_ pid: Int32) -> Bool {
            var p = parentOf[pid]
            while let q = p, q != me {
                if trustedRoots.contains(q) { return true }
                p = parentOf[q]
            }
            return false
        }

        var samples: [ProcessKey: UInt64] = [:]
        for n in nodes {
            if let cpu = ProcessTree.cpuNanos(n.key.pid) { samples[n.key] = cpu }
            guard checked[n.key] == nil, let path = ProcessTree.path(of: n.key.pid) else { continue }
            let verdict = processGuard.policy.evaluate(path)
            checked[n.key] = verdict
            if verdict.isAllowed || underTrustedRoot(n.key.pid) { continue }
            let why: String
            switch verdict {
            case .refused(let r): why = r
            case .needsTrust: why = "not trusted"
            case .allowed: continue
            }
            if enforce {
                processGuard.log.alert("stopped pid \(n.key.pid) \(path): started by a program of Fling, \(why)")
                terminate(n.key)
                processGuard.onEvent?(.stopped(pid: n.key.pid, executable: path, reason: .unchecked(why)))
            } else {
                processGuard.log.once("child:\(path):\(why)", "WARN",
                                      "report-only: would stop pid \(n.key.pid) \(path): \(why)")
            }
        }
        let live = Set(nodes.map(\.key))
        checked = checked.filter { live.contains($0.key) }

        let over = logic.observe(samples, now: ProcessInfo.processInfo.systemUptime,
                                 cpuCount: ProcessInfo.processInfo.activeProcessorCount)
        for key in over where key.pid != me {
            let path = ProcessTree.path(of: key.pid) ?? "pid \(key.pid)"
            if enforce {
                processGuard.log.alert("stopped pid \(key.pid) \(path): over 30% of the CPU for 90 s")
                terminate(key)
                processGuard.onEvent?(.stopped(pid: key.pid, executable: path, reason: .cpu))
            } else {
                processGuard.log.warn("report-only: pid \(key.pid) \(path) over 30% of the CPU for 90 s")
            }
        }
    }

    /// SIGTERM, then SIGKILL after 5 s if the same process is still there.
    private func terminate(_ key: ProcessKey) {
        guard key.pid > 1, key.pid != getpid() else { return }
        kill(key.pid, SIGTERM)
        queue.asyncAfter(deadline: .now() + 5) {
            if ProcessTree.startTime(key.pid) == key.start { kill(key.pid, SIGKILL) }
        }
    }
}
