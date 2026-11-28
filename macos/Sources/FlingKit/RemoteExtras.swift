// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port (from the Windows / Linux build, desktop/fling_remote.py): the pure parts of the
/// remote's extras — remote modes, the touchpad, favourite apps, scenes and the sleep
/// timer. Everything here goes over the Android TV Remote protocol; nothing needs ADB.

/// Android key codes the upstream `ATVKeyCode` table doesn't list (the remote protocol
/// uses the same numbers as Android's KeyEvent).
public enum TVKey {
    public static let stop: Int32 = 86, next: Int32 = 87, previous: Int32 = 88
    public static let rewind: Int32 = 89, fastForward: Int32 = 90
    public static let info: Int32 = 165, channelUp: Int32 = 166, channelDown: Int32 = 167
    public static let guide: Int32 = 172, captions: Int32 = 175, settings: Int32 = 176
    public static let input: Int32 = 178
    public static let red: Int32 = 183, green: Int32 = 184, yellow: Int32 = 185, blue: Int32 = 186
    public static let audioTrack: Int32 = 222, sleep: Int32 = 223, wakeUp: Int32 = 224

    /// KEYCODE_0 … KEYCODE_9.
    public static func digit(_ d: Int) -> Int32 { Int32(7 + min(9, max(0, d))) }

    /// Keys a scene step can press: (id stored in the scene, English title, code).
    public static let sceneKeys: [(id: String, title: String, code: Int32)] = [
        ("home", "Home", ATVKeyCode.home), ("back", "Back", ATVKeyCode.back),
        ("ok", "OK", ATVKeyCode.dpadCenter), ("play_pause", "Play / Pause", ATVKeyCode.mediaPlayPause),
        ("mute", "Mute", ATVKeyCode.volumeMute), ("input", "Input", input),
        ("settings", "TV settings", settings),
    ]
}

/// The pages under the app grid. Stored in UserDefaults under `RemoteMode.defaultsKey`
/// (KeyRemote reads it too: in Numbers the Mac's digit keys type digits on the TV).
public enum RemoteMode: String, CaseIterable, Sendable {
    case dpad, touch, num, media
    public static let defaultsKey = "remoteMode"
    public var title: String {
        switch self {
        case .dpad: return "Arrows"
        case .touch: return "Touchpad"
        case .num: return "Numbers"
        case .media: return "Media"
        }
    }
    public static var current: RemoteMode {
        RemoteMode(rawValue: UserDefaults.standard.string(forKey: defaultsKey) ?? "") ?? .dpad
    }
}

/// Drag on the touchpad like on a laptop's: every `step` points along the main direction
/// is one arrow press; the other axis' drift is dropped (Windows TouchPad, STEP = 34).
public struct TouchpadNav {
    public static let step: CGFloat = 34
    private var accX: CGFloat = 0
    private var accY: CGFloat = 0
    public init() {}

    /// `dx` / `dy` in view points, y growing downwards. Returns keycodes, oldest first.
    public mutating func drag(dx: CGFloat, dy: CGFloat) -> [Int32] {
        accX += dx
        accY += dy
        var keys: [Int32] = []
        while keys.count < 4 {
            if abs(accX) >= Self.step && abs(accX) >= abs(accY) {
                keys.append(accX > 0 ? ATVKeyCode.dpadRight : ATVKeyCode.dpadLeft)
                accX -= accX > 0 ? Self.step : -Self.step
                accY = 0
            } else if abs(accY) >= Self.step {
                keys.append(accY > 0 ? ATVKeyCode.dpadDown : ATVKeyCode.dpadUp)
                accY -= accY > 0 ? Self.step : -Self.step
                accX = 0
            } else {
                break
            }
        }
        return keys
    }

    public mutating func reset() { accX = 0; accY = 0 }
}

// MARK: - favourite apps

/// One chip of the app grid. Codable so the user's set is kept in UserDefaults.
public struct FavoriteApp: Codable, Hashable, Identifiable, Sendable {
    public var name: String
    public var link: String
    public var package: String

    public var id: String { package.isEmpty ? link : package }

    public init(name: String, link: String, package: String) {
        self.name = name; self.link = link; self.package = package
    }

    public init(_ app: TVApp) { self.init(name: app.name, link: app.link, package: app.package) }

    /// A package the TV reported (no known app link): opened the way androidtvremote2 opens
    /// a bare app id, `market://launch?id=…`.
    public init(package: String) {
        self.init(name: TVApp.displayName(forPackage: package),
                  link: FavoriteApps.launchLink(forPackage: package), package: package)
    }

    public var tvApp: TVApp { TVApp(name: name, link: link, package: package) }
}

public enum FavoriteApps {
    public static let maxCount = 15
    /// Home-screen launchers: never offered as "the app open on the TV".
    public static let launchers: Set<String> = [
        "com.google.android.tvlauncher", "com.google.android.apps.tv.launcherx",
        "com.google.android.apps.tv.launcher", "com.google.android.leanbacklauncher",
    ]

    public static var defaults: [FavoriteApp] { TVApp.catalog.map(FavoriteApp.init) }

    public static func launchLink(forPackage package: String) -> String {
        "market://launch?id=" + package
    }

    /// Android package names: `org.videolan.vlc`. Also what makes a typed name safe to send.
    public static func isPackageName(_ s: String) -> Bool {
        s.range(of: #"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$"#, options: .regularExpression) != nil
    }

    private static func key(_ tv: String) -> String { "favApps." + tv }

    /// The TV's set, kept under its name (survives a new IP from the router); the catalog
    /// until the user changes something.
    public static func load(tv: String, from store: UserDefaults = .standard) -> [FavoriteApp] {
        guard let data = store.data(forKey: key(tv)),
              let apps = try? JSONDecoder().decode([FavoriteApp].self, from: data) else { return defaults }
        return apps
    }

    /// nil restores the default set.
    public static func save(_ apps: [FavoriteApp]?, tv: String, to store: UserDefaults = .standard) {
        guard let apps else { store.removeObject(forKey: key(tv)); return }
        let clean = apps.prefix(maxCount).map {
            FavoriteApp(name: String($0.name.prefix(40)), link: $0.link, package: $0.package)
        }
        if let data = try? JSONEncoder().encode(Array(clean)) { store.set(data, forKey: key(tv)) }
    }

    /// Adds `app` unless it is there already or the grid is full; nil when nothing changed.
    public static func adding(_ app: FavoriteApp, to apps: [FavoriteApp]) -> [FavoriteApp]? {
        guard apps.count < maxCount, !apps.contains(where: { $0.id == app.id }) else { return nil }
        return apps + [app]
    }

    public static func moving(_ apps: [FavoriteApp], at i: Int, by d: Int) -> [FavoriteApp] {
        var out = apps
        let j = i + d
        guard out.indices.contains(i), out.indices.contains(j) else { return apps }
        out.swapAt(i, j)
        return out
    }
}

// MARK: - scenes

/// One step of a scene ("Movie night": turn on the TV → open an app → volume 20 %).
public enum SceneStep: Codable, Hashable, Sendable {
    case wake
    case off
    case app(FavoriteApp)
    case volume(percent: Int)
    case key(String)                 // a `TVKey.sceneKeys` id
    case wait(seconds: Int)
    case sleepTimer(minutes: Int)

    /// English; the UI translates the pieces (`tr("Open: ") + name`).
    public var parts: (String, String?) {
        switch self {
        case .wake: return ("Turn on the TV", nil)
        case .off: return ("Turn off the TV", nil)
        case .app(let a): return ("Open: ", a.name)
        case .volume(let p): return ("TV volume: ", "\(p)%")
        case .key(let id): return ("Button: ", TVKey.sceneKeys.first { $0.id == id }?.title ?? id)
        case .wait(let s): return ("Wait: ", "\(s) s")
        case .sleepTimer(let m): return ("Sleep timer: ", "\(m) min")
        }
    }
}

public struct TVScene: Codable, Hashable, Identifiable, Sendable {
    public var id = UUID()
    public var name: String
    /// Switch to this TV (by name) first; nil = whichever TV is selected.
    public var tv: String?
    public var steps: [SceneStep]

    public init(name: String, tv: String? = nil, steps: [SceneStep]) {
        self.name = name; self.tv = tv; self.steps = steps
    }

    /// What "New scene…" starts from.
    public static func template(app: FavoriteApp?) -> TVScene {
        var steps: [SceneStep] = [.wake]
        if let app { steps.append(.app(app)) }
        steps.append(.volume(percent: 20))
        return TVScene(name: "Movie night", steps: steps)
    }
}

public enum SceneStore {
    public static let defaultsKey = "scenes"

    public static func load(from store: UserDefaults = .standard) -> [TVScene] {
        guard let data = store.data(forKey: defaultsKey),
              let scenes = try? JSONDecoder().decode([TVScene].self, from: data) else { return [] }
        return scenes
    }

    public static func save(_ scenes: [TVScene], to store: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(scenes) { store.set(data, forKey: defaultsKey) }
    }

    /// Names stay unique (case-insensitively): "Movie night" twice becomes "Movie night 2".
    public static func uniqueName(_ name: String, among others: [TVScene]) -> String {
        let base = String(name.trimmingCharacters(in: .whitespaces).prefix(60))
        let taken = Set(others.map { $0.name.lowercased() })
        guard taken.contains(base.lowercased()) else { return base }
        var n = 2
        while taken.contains("\(base) \(n)".lowercased()) { n += 1 }
        return "\(base) \(n)"
    }
}

// MARK: - sleep timer

public enum SleepTimerMath {
    public static let choices = [15, 30, 45, 60, 90, 120]

    /// Minutes shown for what is left: rounded up, at least 1 while running.
    public static func minutesLeft(until deadline: Date, now: Date = Date()) -> Int {
        max(1, Int((deadline.timeIntervalSince(now) / 60).rounded(.up)))
    }
}
