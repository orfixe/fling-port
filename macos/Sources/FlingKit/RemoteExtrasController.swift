// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Combine

/// Port (Windows `fling_remote.Remote`): explicit Turn on / Turn off, the per-TV favourite
/// apps, scenes and the sleep timer — on top of AppState, over the Android TV Remote
/// session. One per AppState (`RemoteExtrasController.of(state)`); it outlives the panel,
/// so the sleep timer runs with the panel closed. No polling unless a timer or a scene runs.
@MainActor
public final class RemoteExtrasController: ObservableObject {
    public let state: AppState
    private let store: UserDefaults

    @Published public private(set) var scenes: [TVScene]
    /// Bumped when a favourites list is saved, so the grid redraws.
    @Published public private(set) var favoritesRevision = 0
    @Published public private(set) var sleepDeadline: Date?
    @Published public private(set) var runningScene: String?

    private var sleepHost: String?
    private var sleepTask: Task<Void, Never>?
    private var sceneTask: Task<Void, Never>?
    private var deviceSink: AnyCancellable?

    private static var instances: [ObjectIdentifier: RemoteExtrasController] = [:]

    public static func of(_ state: AppState) -> RemoteExtrasController {
        if let c = instances[ObjectIdentifier(state)] { return c }
        let c = RemoteExtrasController(state: state)
        instances[ObjectIdentifier(state)] = c
        return c
    }

    public init(state: AppState, store: UserDefaults = .standard) {
        self.state = state
        self.store = store
        self.scenes = SceneStore.load(from: store)
        // a timer set for one TV must not turn off another one
        deviceSink = state.$selectedDevice.removeDuplicates().sink { [weak self] device in
            guard let self, self.sleepDeadline != nil, let host = self.sleepHost,
                  device?.ip != host else { return }
            self.setSleep(minutes: nil)
            self.state.lastError = "Sleep timer cancelled: another TV was selected."
        }
    }

    // MARK: - TV identity

    /// Per-TV settings are kept under the TV's name: it survives a new IP from the router.
    public var tvID: String { state.selectedDevice?.name ?? state.selectedDevice?.ip ?? "" }

    // MARK: - power

    /// POWER toggles, so it is sent only when the TV itself has just reported the opposite
    /// state; otherwise the one-way WAKEUP / SLEEP keys. Unpaired: Cast wakes the TV.
    @discardableResult
    public func turnOn() async -> Bool {
        if state.tvPaired {
            if state.tvIsOn != true {
                await state.pressTVKey(state.tvIsOn == false ? ATVKeyCode.power : TVKey.wakeUp)
            }
            return true
        }
        await state.wakeTV()
        return state.selectedDevice != nil
    }

    @discardableResult
    public func turnOff() async -> Bool {
        guard state.tvPaired else {
            state.lastError = "Can't turn the TV off: the remote isn't paired with it."
            return false
        }
        if state.tvIsOn != false {
            await state.pressTVKey(state.tvIsOn == true ? ATVKeyCode.power : TVKey.sleep)
        }
        return true
    }

    // MARK: - favourite apps

    public var favorites: [FavoriteApp] { FavoriteApps.load(tv: tvID, from: store) }

    /// nil restores the default set.
    public func setFavorites(_ apps: [FavoriteApp]?) {
        FavoriteApps.save(apps, tv: tvID, to: store)
        favoritesRevision += 1
    }

    @discardableResult
    public func addFavorite(_ app: FavoriteApp) -> Bool {
        let apps = favorites
        if apps.count >= FavoriteApps.maxCount {
            state.lastError = "No more room — remove an app first."
            return false
        }
        guard let next = FavoriteApps.adding(app, to: apps) else { return false }
        setFavorites(next)
        return true
    }

    public func launch(_ app: FavoriteApp) async {
        guard state.tvPaired else {
            state.lastError = "Apps open once the remote is paired with the TV."
            return
        }
        await state.launchTVApp(app.tvApp)
    }

    // MARK: - sleep timer

    public var sleepMinutesLeft: Int? { sleepDeadline.map { SleepTimerMath.minutesLeft(until: $0) } }

    /// nil cancels. Wall-clock deadline, checked every ≤30 s, so a Mac that slept through it
    /// still turns the TV off when it wakes.
    public func setSleep(minutes: Int?) {
        sleepTask?.cancel()
        sleepTask = nil
        guard let minutes, minutes > 0 else {
            sleepDeadline = nil
            sleepHost = nil
            return
        }
        let deadline = Date().addingTimeInterval(TimeInterval(minutes) * 60)
        sleepDeadline = deadline
        sleepHost = state.selectedDevice?.ip
        sleepTask = Task { [weak self] in
            while !Task.isCancelled {
                let left = deadline.timeIntervalSinceNow
                if left <= 0 { break }
                try? await Task.sleep(nanoseconds: UInt64(min(left, 30) * 1_000_000_000) + 50_000_000)
                guard !Task.isCancelled else { return }
                self?.objectWillChange.send()          // the "turns off in N min" line
            }
            guard !Task.isCancelled, let self else { return }
            self.sleepDeadline = nil
            self.sleepHost = nil
            self.sleepTask = nil
            await self.turnOff()
        }
    }

    // MARK: - scenes

    public func saveScenes(_ scenes: [TVScene]) {
        self.scenes = scenes
        SceneStore.save(scenes, to: store)
    }

    /// Saves `scene` (new when its id isn't there yet) with a name no other scene has.
    public func save(scene: TVScene) {
        var sc = scene
        var all = scenes
        sc.name = SceneStore.uniqueName(sc.name, among: all.filter { $0.id != sc.id })
        if let i = all.firstIndex(where: { $0.id == sc.id }) { all[i] = sc } else { all.append(sc) }
        saveScenes(all)
    }

    public func delete(scene id: UUID) {
        saveScenes(scenes.filter { $0.id != id })
    }

    public func stopScene() {
        sceneTask?.cancel()
        sceneTask = nil
        runningScene = nil
    }

    /// Runs the steps in order; a new scene replaces a running one.
    public func run(_ scene: TVScene) {
        sceneTask?.cancel()
        runningScene = scene.name
        sceneTask = Task { [weak self] in
            await self?.perform(scene)
            guard !Task.isCancelled else { return }
            self?.runningScene = nil
            self?.sceneTask = nil
        }
    }

    private func pause(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
    }

    /// Polls `cond` every 250 ms until it holds or `timeout` passes.
    private func wait(_ timeout: Double, until cond: () -> Bool) async {
        let end = Date().addingTimeInterval(timeout)
        while !Task.isCancelled, !cond(), Date() < end { await pause(0.25) }
    }

    private func note(_ scene: TVScene, _ msg: String) {
        state.lastError = "Scene “\(scene.name)”: \(msg)"
    }

    private func perform(_ scene: TVScene) async {
        if let want = scene.tv, state.selectedDevice?.name != want {
            if let d = state.devices.first(where: { $0.name == want }) {
                await state.select(device: d)
                await pause(1)
            } else {
                state.lastError = "TV “\(want)” not found — the scene ran on the current TV."
            }
        }
        for step in scene.steps {
            if Task.isCancelled { return }
            switch step {
            case .wake:
                let paired = state.tvPaired
                if await turnOn() {
                    // a paired TV pushes is_on once it is up; one woken over Cast gets a few seconds
                    await wait(paired ? 12 : 4) { paired && state.tvIsOn == true }
                    await pause(1.5)
                } else {
                    await pause(0.4)
                }
            case .off:
                await turnOff()
                await pause(1)
            case .app(let app):
                await launch(app)
                await pause(2.5)
            case .key(let id):
                if let k = TVKey.sceneKeys.first(where: { $0.id == id }) { await state.pressTVKey(k.code) }
                await pause(0.5)
            case .wait(let s):
                await pause(Double(min(600, max(0, s))))
            case .sleepTimer(let m):
                setSleep(minutes: m)
                await pause(0.4)
            case .volume(let pct):
                await wait(6) { state.tvVolume != nil }
                guard let v = state.tvVolume else {
                    note(scene, "the TV doesn't report its volume — step skipped.")
                    continue
                }
                let level = Int((Double(v.max) * Double(min(100, max(0, pct))) / 100).rounded())
                state.setTVVolume(level)
                await pause(0.3)
                await wait(15) { state.tvVolumeTarget == nil }
                await pause(0.5)
            }
        }
    }
}
