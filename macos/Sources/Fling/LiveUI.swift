// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import SwiftUI
import FlingKit

/// Port: LIVE on the Mac — "TV screen in its own window" and "Mouse on the TV, no picture"
/// (scrcpy, FlingKit/LiveScreen.swift): the panel rows, the right-click menu, the messages.
@MainActor
enum Live {
    private static weak var state: AppState?

    static let screen: TVScreen = {
        let s = TVScreen()
        s.title = { mode in
            mode == .screen ? tr("Fling — TV screen")
                            : tr("Fling — TV cursor: click here, ⌥ or ⌘ gives the mouse back")
        }
        s.onError = { error in Live.report(error) }
        s.onUntypable = { text in
            // letters scrcpy can't type (Russian, accented) go through the TV remote
            guard let state = Live.state else { return }
            Task { await state.sendTVText(text) }
        }
        s.onStarted = { pid, _ in
            // the keyboard goes to the TV as soon as the picture opens: bring scrcpy's window
            // forward (Fling is a menu-bar app, so the new window would open behind)
            Task { @MainActor in
                for wait in [0.8, 1.2, 2.0] {
                    try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                    NSRunningApplication(processIdentifier: pid)?.activate()
                }
            }
        }
        // stop it cleanly on quit — only the scrcpy Fling started itself
        NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification,
                                               object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { s.stopForQuit() }
        }
        return s
    }()

    static func bind(_ state: AppState) -> TVScreen {
        Live.state = state
        return screen
    }

    static func toggle(_ mode: LiveMode, _ state: AppState) {
        state.lastError = nil
        bind(state).toggle(mode, ip: state.selectedDevice?.ip)
    }

    static func set(_ change: (inout LiveOptions) -> Void, _ state: AppState) {
        var o = screen.options
        change(&o)
        bind(state).setOptions(o, ip: state.selectedDevice?.ip)
    }

    private static func report(_ error: LiveError) {
        guard let state else { return }
        state.lastError = message(error)
    }

    static func message(_ error: LiveError) -> String {
        switch error {
        case .noTV:
            return tr("No device selected")
        case .noScrcpy:
            copy(LiveTools.installScrcpy)
            return tr("The TV screen needs scrcpy. In Terminal run: brew install scrcpy (copied).")
        case .noADB:
            copy(LiveTools.installADB)
            return tr("The TV screen works through ADB, and adb isn't installed. In Terminal run: brew install --cask android-platform-tools (copied).")
        case .unauthorized:
            return tr("TV screen: press “Allow” for debugging on the TV, then try again.")
        case .notConnected:
            return tr("The TV screen works through ADB: turn on network debugging on the TV (Settings ▸ Developer options), then try again.")
        case .noUHID:
            return tr("This TV has no cursor of its own for the mouse — use “TV screen in its own window” instead.")
        case .failedToStart(let why):
            return tr("The TV screen didn't open: ") + why
        case .closed(let why):
            return tr("The TV screen closed: ") + why
        }
    }

    private static func copy(_ s: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(s, forType: .string)
    }
}

// MARK: - panel

/// Two rows and an options menu above the panel's housekeeping.
struct LiveRows: View {
    @ObservedObject var state: AppState
    @ObservedObject private var screen: TVScreen = Live.screen

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            MenuRow(title: screen.running == .screen ? "Close the TV screen" : "TV screen in its own window",
                    icon: "rectangle.inset.filled.on.rectangle",
                    shortcut: screen.busy ? "…" : nil) {
                Live.toggle(.screen, state)
            }
            MenuRow(title: screen.running == .mouseOnly ? "Close the mouse on the TV" : "Mouse on the TV, no picture",
                    icon: "cursorarrow.motionlines") {
                Live.toggle(.mouseOnly, state)
            }
            options
                .padding(.horizontal, 14).padding(.vertical, 3)
        }
    }

    private var options: some View {
        Menu {
            Picker(tr("Quality"), selection: binding(\.quality)) {
                ForEach(LiveQuality.allCases, id: \.self) { q in
                    Text(tr(q.title) + " — " + tr(q.detail)).tag(q)
                }
            }
            .pickerStyle(.inline)
            Picker(tr("Mouse"), selection: binding(\.mouse)) {
                ForEach(LiveMouse.allCases, id: \.self) { m in Text(tr(m.title)).tag(m) }
            }
            .pickerStyle(.inline)
            Toggle(tr("Keyboard to the TV"), isOn: binding(\.keyboardToTV))
            Toggle(tr("TV sound on this Mac (Android 11+)"), isOn: binding(\.sound))
        } label: {
            Text(tr("Quality") + ": " + tr(screen.options.quality.title) + " · " + tr(screen.options.mouse.title))
                .font(.system(size: 11))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .foregroundStyle(.secondary)
        .help(tr("scrcpy takes these at start: changing one reopens the TV screen"))
    }

    private func binding<V>(_ key: WritableKeyPath<LiveOptions, V>) -> Binding<V> {
        Binding(get: { screen.options[keyPath: key] },
                set: { v in Live.set({ $0[keyPath: key] = v }, state) })
    }
}

// MARK: - right-click menu

enum LiveMenu {
    @MainActor
    static func item(state: AppState) -> NSMenuItem {
        let screen = Live.bind(state)
        let parent = NSMenuItem(title: tr("TV screen (LIVE)"), action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false
        sub.addItem(action("TV screen in its own window", on: screen.running == .screen) {
            Live.toggle(.screen, state)
        })
        sub.addItem(action("Mouse on the TV, no picture", on: screen.running == .mouseOnly) {
            Live.toggle(.mouseOnly, state)
        })
        sub.addItem(.separator())
        for q in LiveQuality.allCases {
            sub.addItem(action(tr(q.title) + " — " + tr(q.detail), on: screen.options.quality == q) {
                Live.set({ $0.quality = q }, state)
            })
        }
        sub.addItem(.separator())
        for m in LiveMouse.allCases {
            sub.addItem(action(m.title, on: screen.options.mouse == m) { Live.set({ $0.mouse = m }, state) })
        }
        sub.addItem(.separator())
        sub.addItem(action("Keyboard to the TV", on: screen.options.keyboardToTV) {
            Live.set({ $0.keyboardToTV.toggle() }, state)
        })
        sub.addItem(action("TV sound on this Mac (Android 11+)", on: screen.options.sound) {
            Live.set({ $0.sound.toggle() }, state)
        })
        parent.submenu = sub
        return parent
    }

    private static func action(_ title: String, on: Bool, handler: @escaping () -> Void) -> NSMenuItem {
        let item = NSMenuItem(title: tr(title), action: #selector(Trampoline.fire), keyEquivalent: "")
        let t = Trampoline(handler)
        item.target = t
        item.representedObject = t
        item.state = on ? .on : .off
        return item
    }
}

// MARK: - Russian

/// LIVE's phrases, looked up by tr() after the main table (kept here so the other
/// port areas don't all edit the same table).
enum LiveL10n {
    static let ru: [String: String] = [
        "TV screen (LIVE)": "Экран ТВ (LIVE)",
        "TV screen in its own window": "Экран ТВ в отдельном окне",
        "Mouse on the TV, no picture": "Мышь на ТВ без картинки",
        "Close the TV screen": "Закрыть экран ТВ",
        "Close the mouse on the TV": "Закрыть мышь на ТВ",
        "Fling — TV screen": "Fling — экран ТВ",
        "Fling — TV cursor: click here, ⌥ or ⌘ gives the mouse back":
            "Fling — курсор: щёлкните здесь, ⌥ или ⌘ — вернуть мышь",
        "Quality": "Качество",
        "Auto": "Авто",
        "Sharp": "Чётко",
        "Fast": "Быстро",
        "Saver": "Экономно",
        "1280 px, 60 frames/s": "1280 px, 60 кадров/с",
        "1920 px, 30 frames/s — sharper, a bit more delay": "1920 px, 30 кадров/с — чётче, но задержка больше",
        "960 px, 60 frames/s — the least delay, for games": "960 px, 60 кадров/с — самая маленькая задержка, для игр",
        "960 px, 2 Mbit/s — when the network is slow": "960 px, 2 Мбит/с — когда сеть медленная",
        "Mouse": "Мышь",
        "TV cursor": "Курсор ТВ",
        "Just watch": "Только смотреть",
        "Keyboard to the TV": "Клавиатура — на ТВ",
        "TV sound on this Mac (Android 11+)": "Звук ТВ на этом Mac (Android 11+)",
        "scrcpy takes these at start: changing one reopens the TV screen":
            "Эти настройки применяются при запуске: после изменения экран ТВ откроется заново",
        "The TV screen needs scrcpy. In Terminal run: brew install scrcpy (copied).":
            "Для экрана ТВ нужен scrcpy. Выполните в Терминале: brew install scrcpy (команда скопирована).",
        "The TV screen works through ADB, and adb isn't installed. In Terminal run: brew install --cask android-platform-tools (copied).":
            "Экран ТВ работает через ADB, а adb не установлен. Выполните в Терминале: brew install --cask android-platform-tools (команда скопирована).",
        "TV screen: press “Allow” for debugging on the TV, then try again.":
            "Экран ТВ: подтвердите на ТВ «Разрешить отладку» и попробуйте снова.",
        "The TV screen works through ADB: turn on network debugging on the TV (Settings ▸ Developer options), then try again.":
            "Экран ТВ работает через ADB: включите на ТВ отладку по сети (Настройки ▸ Для разработчиков) и попробуйте снова.",
        "This TV has no cursor of its own for the mouse — use “TV screen in its own window” instead.":
            "У этого ТВ нет своего курсора для мыши — откройте «Экран ТВ в отдельном окне».",
        "The TV screen didn't open: ": "Экран ТВ не открылся: ",
        "The TV screen closed: ": "Экран ТВ закрылся: ",
    ]
}
