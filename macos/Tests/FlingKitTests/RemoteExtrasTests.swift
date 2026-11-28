// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// swift-testing (Xcode 16+). The Command Line Tools on macOS 27 ship neither XCTest nor the
// swift-testing macro plugin, so `swift test` needs full Xcode.
import Foundation
import Testing
@testable import FlingKit

/// No subprocesses: AppState is built without catt here.
private struct NoProcesses: ProcessRunning {
    func run(_ executable: String, _ args: [String], timeout: TimeInterval) throws -> ProcessResult {
        ProcessResult(exitCode: 0, stdout: "", stderr: "", timedOut: false)
    }
    func run(_ executable: String, _ args: [String]) throws -> String { "" }
}

@Suite struct TouchpadNavTests {
    @Test func test_small_drags_accumulate_before_a_step() {
        var nav = TouchpadNav()
        #expect(nav.drag(dx: 20, dy: 0) == [])
        #expect(nav.drag(dx: 20, dy: 0) == [ATVKeyCode.dpadRight])
    }

    @Test func test_dominant_axis_and_direction() {
        var nav = TouchpadNav()
        #expect(nav.drag(dx: 5, dy: -40) == [ATVKeyCode.dpadUp])
        nav.reset()
        #expect(nav.drag(dx: -40, dy: 10) == [ATVKeyCode.dpadLeft])
        nav.reset()
        #expect(nav.drag(dx: 0, dy: 70) == [ATVKeyCode.dpadDown, ATVKeyCode.dpadDown])
    }

    @Test func test_cross_axis_drift_is_dropped_after_a_step() {
        var nav = TouchpadNav()
        #expect(nav.drag(dx: 40, dy: 30) == [ATVKeyCode.dpadRight])
        #expect(nav.drag(dx: 0, dy: 10) == [])          // the 30 of drift is gone
    }

    @Test func test_a_huge_jump_is_capped() {
        var nav = TouchpadNav()
        #expect(nav.drag(dx: 1000, dy: 0).count == 4)
    }
}

@Suite struct FavoriteAppsTests {
    @Test func test_defaults_until_saved_then_per_tv() {
        let name = "fling-tests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        defer { d.removePersistentDomain(forName: name) }
        #expect(FavoriteApps.load(tv: "Living room", from: d) == FavoriteApps.defaults)
        let vlc = FavoriteApp(package: "org.videolan.vlc")
        FavoriteApps.save([vlc], tv: "Living room", to: d)
        #expect(FavoriteApps.load(tv: "Living room", from: d) == [vlc])
        #expect(FavoriteApps.load(tv: "Bedroom", from: d) == FavoriteApps.defaults)
        FavoriteApps.save(nil, tv: "Living room", to: d)
        #expect(FavoriteApps.load(tv: "Living room", from: d) == FavoriteApps.defaults)
    }

    @Test func test_package_app_launches_through_market_link() {
        let a = FavoriteApp(package: "org.videolan.vlc")
        #expect(a.link == "market://launch?id=org.videolan.vlc")
        #expect(a.name == "Vlc")
    }

    @Test func test_adding_skips_duplicates_and_respects_the_limit() {
        let yt = FavoriteApps.defaults[0]
        #expect(FavoriteApps.adding(yt, to: FavoriteApps.defaults) == nil)
        let full = (0..<FavoriteApps.maxCount).map { FavoriteApp(package: "com.example.app\($0)") }
        #expect(FavoriteApps.adding(FavoriteApp(package: "org.kodi.x"), to: full) == nil)
        #expect(FavoriteApps.adding(FavoriteApp(package: "org.kodi.x"), to: [yt])?.count == 2)
    }

    @Test func test_moving_swaps_neighbours_and_ignores_edges() {
        let apps = Array(FavoriteApps.defaults.prefix(3))
        #expect(FavoriteApps.moving(apps, at: 0, by: 1).map(\.id) == [apps[1].id, apps[0].id, apps[2].id])
        #expect(FavoriteApps.moving(apps, at: 0, by: -1) == apps)
        #expect(FavoriteApps.moving(apps, at: 2, by: 1) == apps)
    }

    @Test func test_package_names() {
        #expect(FavoriteApps.isPackageName("org.videolan.vlc"))
        #expect(!FavoriteApps.isPackageName("vlc"))
        #expect(!FavoriteApps.isPackageName("org.videolan.vlc; reboot"))
    }
}

@Suite struct SceneTests {
    @Test func test_scenes_round_trip_through_defaults() {
        let name = "fling-tests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        defer { d.removePersistentDomain(forName: name) }
        let sc = TVScene(name: "Movie night", tv: "Living room",
                         steps: [.wake, .app(FavoriteApps.defaults[0]), .volume(percent: 20), .key("home"),
                                 .wait(seconds: 3), .sleepTimer(minutes: 30), .off])
        SceneStore.save([sc], to: d)
        #expect(SceneStore.load(from: d) == [sc])
    }

    @Test func test_unique_names() {
        let others = [TVScene(name: "Movie night", steps: [.wake]), TVScene(name: "movie night 2", steps: [.wake])]
        #expect(SceneStore.uniqueName("Movie night", among: others) == "Movie night 3")
        #expect(SceneStore.uniqueName("  Morning ", among: others) == "Morning")
    }

    @Test func test_template_and_step_text() {
        let t = TVScene.template(app: FavoriteApps.defaults[0])
        #expect(t.steps == [.wake, .app(FavoriteApps.defaults[0]), .volume(percent: 20)])
        #expect(SceneStep.key("ok").parts.1 == "OK")
        #expect(SceneStep.wait(seconds: 5).parts.1 == "5 s")
    }

    @Test func test_scene_keys_and_digits() {
        #expect(TVKey.digit(0) == 7)
        #expect(TVKey.digit(9) == 16)
        #expect(Set(TVKey.sceneKeys.map(\.id)).count == TVKey.sceneKeys.count)
    }
}

@Suite struct SleepTimerMathTests {
    @Test func test_minutes_left_rounds_up_and_never_shows_zero() {
        let now = Date()
        #expect(SleepTimerMath.minutesLeft(until: now.addingTimeInterval(25 * 60), now: now) == 25)
        #expect(SleepTimerMath.minutesLeft(until: now.addingTimeInterval(24 * 60 + 1), now: now) == 25)
        #expect(SleepTimerMath.minutesLeft(until: now.addingTimeInterval(5), now: now) == 1)
        #expect(SleepTimerMath.minutesLeft(until: now.addingTimeInterval(-5), now: now) == 1)
    }
}

@MainActor @Suite struct RemoteExtrasControllerTests {
    @Test func test_sleep_timer_sets_and_cancels() {
        let state = AppState(catt: nil, browsers: BrowserReader(runner: NoProcesses()))
        let name = "fling-tests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        defer { d.removePersistentDomain(forName: name) }
        let c = RemoteExtrasController(state: state, store: d)
        c.setSleep(minutes: 30)
        #expect(c.sleepMinutesLeft == 30)
        c.setSleep(minutes: nil)
        #expect(c.sleepDeadline == nil)
    }

    @Test func test_turning_off_unpaired_tv_explains_why() async {
        let state = AppState(catt: nil, browsers: BrowserReader(runner: NoProcesses()))
        let name = "fling-tests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        defer { d.removePersistentDomain(forName: name) }
        let c = RemoteExtrasController(state: state, store: d)
        let ok = await c.turnOff()
        #expect(!ok)
        #expect(state.lastError != nil)
    }
}
