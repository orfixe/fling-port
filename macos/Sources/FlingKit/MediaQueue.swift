// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Combine

/// Port (desktop/fling_media.py, class Media): files, folders and playlists from this Mac
/// as a queue on the TV, photo slideshows, subtitles and resume.
///
/// Playing one item = one lasting `catt cast` process: catt serves a local file over HTTP
/// for exactly as long as the TV plays it (`-b` keeps it alive for web links). When the
/// process ends, the last polled position tells whether the item *finished* (go on to the
/// next one) or was stopped / replaced (the queue waits).

/// Order and position of the queue — pure, so the stepping rules are unit-tested.
public struct QueueOrder: Equatable, Sendable {
    public private(set) var order: [Int]
    public private(set) var pos: Int

    public init(count: Int, start: Int = 0, shuffle: Bool = false) {
        order = Array(0..<count)
        pos = count == 0 ? 0 : max(0, min(start, count - 1))
        if shuffle && count > 1 {
            let first = order.remove(at: pos)
            order.shuffle()
            order.insert(first, at: 0)
            pos = 0
        }
    }

    public var count: Int { order.count }
    public var current: Int? { order.indices.contains(pos) ? order[pos] : nil }
    public var isLast: Bool { pos + 1 >= count }

    public enum Step: Equatable, Sendable {
        /// Play `current`; `fromStart` ignores a remembered position.
        case play(fromStart: Bool)
        /// The queue is over.
        case end
    }

    /// `auto`: the item finished on its own (repeat-one replays it); otherwise the user
    /// pressed Next.
    public mutating func advance(auto: Bool, repeatMode: PortSettings.RepeatMode, shuffle: Bool) -> Step {
        guard count > 0 else { return .end }
        if auto && repeatMode == .one { return .play(fromStart: true) }
        if pos + 1 < count {
            pos += 1
        } else if repeatMode == .all {
            if shuffle && count > 2, let last = order.last {
                order.shuffle()
                if order.first == last { order.append(order.removeFirst()) }   // not twice in a row
            }
            pos = 0
        } else {
            return .end
        }
        return .play(fromStart: !auto)
    }

    /// Previous item; false at the first one.
    public mutating func back() -> Bool {
        guard pos > 0 else { return false }
        pos -= 1
        return true
    }

    public mutating func jump(to p: Int) { if order.indices.contains(p) { pos = p } }
}

public enum QueuePhase: String, Sendable { case idle, loading, playing, showing, stopped }

/// The running `catt` child of one queue item.
public protocol QueueProcess: AnyObject { func kill() }

public protocol QueueLaunching {
    /// Starts `catt args`; `onLine` gets stdout+stderr lines, `onExit` the exit code.
    func launch(_ executable: String, _ args: [String],
                onLine: @escaping @Sendable (String) -> Void,
                onExit: @escaping @Sendable (Int32) -> Void) throws -> QueueProcess
}

public struct SystemQueueLauncher: QueueLaunching {
    public init() {}

    final class Child: QueueProcess, @unchecked Sendable {
        let process: Process
        init(_ p: Process) { process = p }
        func kill() { if process.isRunning { process.terminate() } }
    }

    public func launch(_ executable: String, _ args: [String],
                       onLine: @escaping @Sendable (String) -> Void,
                       onExit: @escaping @Sendable (Int32) -> Void) throws -> QueueProcess {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: executable)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        let buffer = LineBuffer(onLine)
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil } else { buffer.feed(d) }
        }
        p.terminationHandler = { proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            buffer.feed(pipe.fileHandleForReading.readDataToEndOfFile())
            buffer.flush()
            onExit(proc.terminationStatus)
        }
        try GuardedProcess.check(executable)   // port: macOS guard
        try p.run()
        return Child(p)
    }

    final class LineBuffer: @unchecked Sendable {
        private let lock = NSLock()
        private var pending = Data()
        private let onLine: @Sendable (String) -> Void
        init(_ onLine: @escaping @Sendable (String) -> Void) { self.onLine = onLine }
        func feed(_ d: Data) {
            lock.lock()
            pending.append(d)
            var lines: [String] = []
            while let nl = pending.firstIndex(of: 0x0A) {
                lines.append(String(decoding: pending[pending.startIndex..<nl], as: UTF8.self))
                pending.removeSubrange(pending.startIndex...nl)
            }
            lock.unlock()
            lines.forEach(onLine)
        }
        func flush() {
            lock.lock(); let rest = pending; pending = Data(); lock.unlock()
            if !rest.isEmpty { onLine(String(decoding: rest, as: UTF8.self)) }
        }
    }
}

@MainActor
public final class MediaQueue: ObservableObject {
    @Published public private(set) var items: [MediaItem] = []
    @Published public private(set) var order = QueueOrder(count: 0)
    @Published public private(set) var phase: QueuePhase = .idle
    /// Slideshow paused by the user (the current photo stays on the TV).
    @Published public private(set) var slidePaused = false
    @Published public private(set) var isSlideshow = false

    /// `catt` path; nil when it isn't installed.
    public var executable: String?
    /// IP of the selected TV.
    public var device: () -> String? = { nil }
    public var language = "en"
    /// A message for the user (a notification); `error` goes to the panel.
    public var notify: (String) -> Void = { _ in }
    public var error: (String) -> Void = { _ in }
    /// Translates the queue's own messages (the app's `tr`).
    public var tr: (String) -> String = { $0 }
    /// Asks for a status poll soon after an item has loaded.
    public var pollSoon: () -> Void = {}

    private let launcher: QueueLaunching
    private let settings: PortSettings
    private var proc: QueueProcess?
    private var gen = 0
    private var playedAny = false
    private var failures = 0
    private var slideTimer: Timer?
    private var itemStarted = Date.distantPast
    // tracking what the TV plays, for resume and "finished"
    private var curKey: String?
    private var lockedTitle: String?
    private var lastPos: (TimeInterval?, TimeInterval?) = (nil, nil)
    private var lastElapsed: TimeInterval = 0

    public init(launcher: QueueLaunching = SystemQueueLauncher(), settings: PortSettings? = nil) {
        self.launcher = launcher
        self.settings = settings ?? .shared
    }

    // MARK: - what the UI asks

    public var count: Int { order.count }
    public var current: MediaItem? { order.current.map { items[$0] } }
    /// More than one thing queued and not cleared: show the queue strip.
    public var isLive: Bool { count > 1 && phase != .idle }
    /// A photo is on the TV: no "Now on TV" notification per photo.
    public var quietTrack: Bool { phase != .idle && current?.kind == .image }
    public var isActive: Bool { [.loading, .playing, .showing].contains(phase) }
    public var positionLabel: String { count > 1 ? "\(order.pos + 1)/\(count)" : "" }

    // MARK: - entry points

    /// Picked / dropped files, folders and playlists.
    public func send(paths: [String]) {
        let r = MediaFiles.expand(paths)
        for p in r.skipped { notify(String(format: tr("Not a media file: %@"), (p as NSString).lastPathComponent)) }
        guard !r.items.isEmpty else { if r.skipped.isEmpty { error("Nothing to play") }; return }
        play(r.items)
    }

    /// All photos of a folder (and its subfolders), one every `slideSeconds`.
    public func startSlideshow(folder: String) {
        let photos = MediaFiles.scanFolder(folder).filter { MediaFiles.kind(ofPath: $0) == .image }
        guard !photos.isEmpty else { error("No photos in this folder"); return }
        play(photos.map { MediaItem(src: $0) }, slideshow: true)
        if photos.count > 1 {
            notify(String(format: tr("Slideshow “%@”: a new photo every %d s"),
                          (folder as NSString).lastPathComponent, settings.slideSeconds))
        }
    }

    public func play(_ list: [MediaItem], slideshow: Bool = false, start: Int = 0) {
        guard !list.isEmpty else { error("Nothing to play"); return }
        halt()
        items = list
        isSlideshow = slideshow
        order = QueueOrder(count: list.count, start: start, shuffle: settings.shuffle && !slideshow)
        playedAny = false
        failures = 0
        slidePaused = false
        if list.count > 1 && !slideshow {
            notify(String(format: tr("Queue: %d items — starting with “%@”"), list.count, current?.name ?? ""))
        }
        startCurrent()
    }

    /// Another cast (a tab, a link) replaces whatever the queue was playing.
    public func beforeOtherCast() {
        guard isActive else { return }
        saveResume()
        halt()
        if count > 1 { phase = .stopped } else { clear() }
        track(nil)
    }

    // MARK: - controls

    public func next() {
        guard count > 0 else { return }
        if order.isLast && settings.repeatMode != .all { notify(tr("That's the last item in the queue")); return }
        step(auto: false)
    }

    /// Back to the start of the item when it's past 5 s, else the previous item.
    public func previous() {
        guard count > 0 else { return }
        if lastElapsed > 5 && current?.kind != .image { saveResume(); startCurrent(at: 0); return }
        guard order.back() else { startCurrent(at: 0); return }
        objectWillChange.send()
        startCurrent(at: 0)
    }

    public func jump(to p: Int) {
        guard count > 0 else { return }
        order.jump(to: p)
        startCurrent(at: 0)
    }

    /// The queue waits after the TV was stopped: carry on with the current item.
    public func resumeQueue() { if phase == .stopped { startCurrent() } }

    public func toggleSlideshowPause() {
        guard isSlideshow else { return }
        slidePaused.toggle()
        if slidePaused { slideTimer?.invalidate() }
        else if phase == .showing { step(auto: true) }
    }

    /// Clears the queue; the TV keeps whatever it shows (Stop casting stops that).
    public func stop() {
        saveResume()
        halt()
        track(nil)
        clear()
    }

    // MARK: - status from the TV (the app's 2 s poll)

    public func observe(_ st: CastStatus) {
        lastElapsed = st.elapsed ?? 0
        guard curKey != nil, phase == .playing, st.hasMedia else { return }
        if let t = st.title {
            if lockedTitle == nil { lockedTitle = t }
            else if t != lockedTitle {                        // the TV plays something else now
                saveResume(); track(nil); return
            }
        }
        if let e = st.elapsed {
            lastPos = (e, st.duration ?? lastPos.1)
            if let key = curKey { settings.storePosition(key, position: e, duration: lastPos.1) }
        }
    }

    /// Finished on its own, judged from the last position seen before the TV went idle.
    public nonisolated static func looksFinished(position: TimeInterval?, duration: TimeInterval?) -> Bool {
        guard let p = position, let d = duration, d > 0 else { return false }
        return p >= d - max(min(25, d * 0.25), d * 0.03)
    }

    // MARK: - the process

    private func clear() {
        items = []
        order = QueueOrder(count: 0)
        phase = .idle
        isSlideshow = false
        slidePaused = false
    }

    private func halt() {
        gen += 1
        slideTimer?.invalidate()
        slideTimer = nil
        proc?.kill()
        proc = nil
    }

    private func track(_ key: String?) {
        curKey = key
        lockedTitle = nil
        lastPos = (nil, nil)
    }

    private func saveResume() {
        guard let key = curKey else { return }
        settings.storePosition(key, position: lastPos.0, duration: lastPos.1)
    }

    private func step(auto: Bool) {
        saveResume()
        switch order.advance(auto: auto, repeatMode: settings.repeatMode, shuffle: settings.shuffle) {
        case .play(let fromStart):
            objectWillChange.send()
            startCurrent(at: fromStart ? 0 : nil)
        case .end:
            halt(); track(nil); clear()
            notify(tr(isSlideshow ? "Slideshow finished" : "Queue finished"))
        }
    }

    /// `at`: nil = resume where it was left off (if remembered), 0 = from the start.
    private func startCurrent(at startAt: TimeInterval? = nil) {
        guard let item = current else { return }
        guard let catt = executable else { error("catt is not installed"); return }
        guard let ip = device() else { error("No device selected"); return }
        halt()
        let g = gen
        let key = MediaFiles.resumeKey(item.src)
        var start = startAt
        if start == nil, item.kind != .image, let r = settings.resumePosition(key) {
            start = r
            notify(String(format: tr("Resuming at %@: %@"), Self.clock(r), item.name))
        }
        track(key)
        phase = .loading
        itemStarted = Date()
        lastElapsed = 0
        let language = self.language
        let launcher = self.launcher
        Task.detached(priority: .userInitiated) { [weak self] in
            // converting photos / subtitles can take a moment: off the main actor
            let built = Self.arguments(for: item, device: ip, start: start, language: language)
            await MainActor.run { [weak self] in
                guard let me = self, g == me.gen else { return }
                guard let args = built else {
                    me.failed(g, item, String(format: me.tr("Couldn't open the picture: %@"), item.name))
                    return
                }
                let isWeb = item.isWeb
                do {
                    me.proc = try launcher.launch(catt, args, onLine: { [weak self] line in
                        let l = line.trimmingCharacters(in: .whitespaces)
                        if l.hasPrefix("Serving local file") || (isWeb && l.hasPrefix("Playing ")) {
                            Task { @MainActor in self?.loaded(g, item) }
                        }
                        if l.hasPrefix("Error:") { Task { @MainActor in self?.lastLine = String(l.dropFirst(6)) } }
                    }, onExit: { [weak self] code in
                        Task { @MainActor in self?.exited(g, item, code) }
                    })
                } catch {
                    me.failed(g, item, error.localizedDescription)
                }
            }
        }
    }

    private var lastLine = ""

    /// The `catt` arguments for one item, nil when a picture can't be decoded.
    public nonisolated static func arguments(for item: MediaItem, device: String, start: TimeInterval?,
                                             language: String) -> [String]? {
        var args = ["-d", device, "cast"]
        var src = item.src
        let name = String(item.name.prefix(200))
        switch item.kind {
        case .image?:
            guard let img = MediaFiles.prepareImage(item.src) else { return nil }
            src = img
            args += ["--no-subs", "-l", name]
        case .video?:
            args += ["-l", name]
            let sub = item.subtitles == "off" ? nil
                : (item.subtitles ?? MediaFiles.findSubtitles(forVideo: item.src, language: language))
            if let sub, let vtt = MediaFiles.prepareSubtitles(sub) { args += ["-s", vtt] } else { args.append("--no-subs") }
        case nil where item.isWeb:
            args.append("-b")
            if URLClassifier.classify(item.src) == .directMedia { args.append("-f") }
        default:
            args += ["--no-subs", "-l", name]
        }
        if let start, start > 1, item.kind != .image { args += ["-t", String(Int(start))] }
        args.append(src)
        return args
    }

    private func loaded(_ g: Int, _ item: MediaItem) {
        guard g == gen, phase == .loading else { return }
        phase = .playing
        failures = 0
        playedAny = true
        pollSoon()
    }

    private func exited(_ g: Int, _ item: MediaItem, _ code: Int32) {
        guard g == gen else { return }                  // stopped on purpose
        proc = nil
        if code != 0 {
            failed(g, item, lastLine.isEmpty ? "catt \(code)" : lastLine.trimmingCharacters(in: .whitespaces))
            return
        }
        if item.kind == .image {
            playedAny = true
            failures = 0
            phase = .showing
            if count > 1 && (!order.isLast || settings.repeatMode != .off) && !slidePaused {
                slideTimer = Timer.scheduledTimer(withTimeInterval: TimeInterval(settings.slideSeconds),
                                                  repeats: false) { [weak self] _ in
                    Task { @MainActor in
                        guard let self, g == self.gen else { return }
                        self.step(auto: true)
                    }
                }
            }
            return
        }
        // the TV went idle: finished, or stopped / replaced? Let the last status arrive.
        Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(1200))
            self?.decide(g)
        }
    }

    private func decide(_ g: Int) {
        guard g == gen else { return }
        let finished = Self.looksFinished(position: lastPos.0, duration: lastPos.1)
        if finished, let key = curKey {
            settings.storePosition(key, position: lastPos.1, duration: lastPos.1)   // watched: drop the bookmark
        } else {
            saveResume()
        }
        if finished && (count > 1 || settings.repeatMode == .one) { step(auto: true); return }
        track(nil)
        if count > 1 && !finished { phase = .stopped } else { clear() }
    }

    private func failed(_ g: Int, _ item: MediaItem, _ err: String) {
        guard g == gen else { return }
        proc = nil
        let lower = err.lowercased()
        let why = lower.contains("failed to load media") || lower.contains("codec") || lower.contains("has failed")
            ? tr("The TV can't play this format or codec") : tr(err)
        if count <= 1 {
            track(nil); clear()
            error(String(format: tr("Couldn't play %@: %@"), item.name, why))
            return
        }
        failures += 1
        if failures >= 3 || (!playedAny && failures >= 2) {
            halt()
            phase = .stopped
            error(String(format: tr("Couldn't play %@: %@"), item.name, why))
            return
        }
        notify(String(format: tr("Skipping %@: %@"), item.name, why))
        if order.isLast && settings.repeatMode != .all { halt(); track(nil); clear(); return }
        step(auto: false)
    }

    static func clock(_ t: TimeInterval) -> String {
        let s = Int(t), h = s / 3600, m = (s % 3600) / 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s % 60) : String(format: "%d:%02d", m, s % 60)
    }
}
