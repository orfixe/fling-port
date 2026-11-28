// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import SwiftUI
import AppKit
import FlingKit

/// Port (Windows `fling_remote.more_button` / `SleepRow` / `tray_menu`): the "⋯" key in the
/// remote header — scenes, the sleep timer, Turn on / Turn off — the "turns off in N min"
/// strip, and the same scenes and timer in the menu-bar icon's right-click menu.
struct RemoteMoreMenu: View {
    @ObservedObject var extras: RemoteExtrasController

    init(state: AppState) { extras = RemoteExtrasController.of(state) }

    var body: some View {
        Menu {
            Menu(tr("Scenes")) {
                ForEach(extras.scenes) { sc in
                    Button(sc.name) { extras.run(sc) }
                }
                if extras.scenes.isEmpty { Text(tr("No scenes yet")) }
                if let running = extras.runningScene {
                    Divider()
                    Button(tr("Stop the scene: ") + running) { extras.stopScene() }
                }
                Divider()
                Button(tr("New scene…")) { SceneEditorWindow.open(extras, scene: nil) }
                if !extras.scenes.isEmpty {
                    Menu(tr("Edit")) {
                        ForEach(extras.scenes) { sc in
                            Button(sc.name) { SceneEditorWindow.open(extras, scene: sc) }
                        }
                    }
                }
            }
            Menu(sleepTitle) {
                ForEach(SleepTimerMath.choices, id: \.self) { m in
                    Button(tr("\(m) min")) { extras.setSleep(minutes: m) }
                }
                if extras.sleepDeadline != nil {
                    Divider()
                    Button(tr("Cancel the timer")) { extras.setSleep(minutes: nil) }
                }
            }
            Divider()
            Button(tr("Turn on the TV")) { Task { await extras.turnOn() } }
            Button(tr("Turn off the TV")) { Task { await extras.turnOff() } }
        } label: {
            Image(systemName: "ellipsis").font(.system(size: 11, weight: .semibold))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        // the borderless menu drops a label's background, so the circle sits behind it
        .frame(width: 26, height: 26)
        .background(Circle().fill(Color.primary.opacity(0.07)))
        .contentShape(Circle())
        .help(tr("More: scenes, sleep timer, power"))
    }

    private var sleepTitle: String {
        guard let left = extras.sleepMinutesLeft else { return tr("Sleep timer") }
        return tr("Sleep timer") + " — " + tr("\(left) min")
    }
}

/// "☾ Sleep timer: the TV turns off in 25 min   [Cancel]" under the remote header.
struct SleepTimerRow: View {
    @ObservedObject var extras: RemoteExtrasController

    init(state: AppState) { extras = RemoteExtrasController.of(state) }

    var body: some View {
        if let deadline = extras.sleepDeadline {
            TimelineView(.periodic(from: .now, by: 15)) { ctx in
                HStack(spacing: 6) {
                    Image(systemName: "moon.fill").font(.system(size: 10))
                    Text(tr("The TV turns off in ")
                         + tr("\(SleepTimerMath.minutesLeft(until: deadline, now: ctx.date)) min"))
                        .font(.system(size: 10.5, weight: .semibold))
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                    Spacer(minLength: 4)
                    Button(tr("Cancel")) { extras.setSleep(minutes: nil) }
                        .help(tr("Cancel the timer"))
                        .buttonStyle(.plain)
                        .font(.system(size: 10.5, weight: .semibold))
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 5))
                }
                .foregroundStyle(Color.accentColor)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
            }
            .padding(.horizontal, 12).padding(.bottom, 6)
        }
    }
}

/// Scenes and the sleep timer for the menu-bar icon's right-click menu: no need to open
/// the panel at night.
@MainActor
enum RemoteStatusMenu {
    static func items(state: AppState) -> [NSMenuItem] {
        let extras = RemoteExtrasController.of(state)
        let scenes = NSMenu()
        scenes.autoenablesItems = false
        for sc in extras.scenes { scenes.addItem(item(sc.name) { extras.run(sc) }) }
        if extras.scenes.isEmpty {
            let none = item(tr("No scenes yet")) {}
            none.isEnabled = false
            scenes.addItem(none)
        }
        if let running = extras.runningScene {
            scenes.addItem(.separator())
            scenes.addItem(item(tr("Stop the scene: ") + running) { extras.stopScene() })
        }
        scenes.addItem(.separator())
        scenes.addItem(item(tr("New scene…")) { SceneEditorWindow.open(extras, scene: nil) })
        if !extras.scenes.isEmpty {
            let edit = NSMenu()
            for sc in extras.scenes { edit.addItem(item(sc.name) { SceneEditorWindow.open(extras, scene: sc) }) }
            let editItem = NSMenuItem(title: tr("Edit"), action: nil, keyEquivalent: "")
            editItem.submenu = edit
            scenes.addItem(editItem)
        }
        let scenesItem = NSMenuItem(title: tr("Scenes"), action: nil, keyEquivalent: "")
        scenesItem.submenu = scenes

        let timer = NSMenu()
        timer.autoenablesItems = false
        for m in SleepTimerMath.choices { timer.addItem(item(tr("\(m) min")) { extras.setSleep(minutes: m) }) }
        if extras.sleepDeadline != nil {
            timer.addItem(.separator())
            timer.addItem(item(tr("Cancel the timer")) { extras.setSleep(minutes: nil) })
        }
        var title = tr("Sleep timer")
        if let left = extras.sleepMinutesLeft { title += " — " + tr("\(left) min") }
        let timerItem = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        timerItem.submenu = timer
        return [scenesItem, timerItem]
    }

    private static func item(_ title: String, _ handler: @escaping () -> Void) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: #selector(Trampoline.fire), keyEquivalent: "")
        let t = Trampoline(handler)
        it.target = t
        it.representedObject = t          // keeps the trampoline alive
        return it
    }
}

// MARK: - scene editor

/// A small window (a dialog over the popover would close it): name, "switch to this TV
/// first", the steps in order, add / move / delete, Run, Save.
@MainActor
enum SceneEditorWindow {
    private static var window: NSWindow?

    static func open(_ extras: RemoteExtrasController, scene: TVScene?) {
        window?.close()
        let isNew = scene == nil
        let start = scene ?? {
            var t = TVScene.template(app: extras.favorites.first { $0.package == extras.state.tvCurrentApp }
                                     ?? extras.favorites.first)
            t.name = tr(t.name)
            return t
        }()
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 500),
                         styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        w.title = tr("Fling — scene")
        w.isReleasedWhenClosed = false
        w.contentViewController = NSHostingController(rootView: SceneEditorView(
            extras: extras, scene: start, isNew: isNew, close: { [weak w] in w?.close() }))
        w.setContentSize(NSSize(width: 460, height: 500))
        w.center()
        window = w
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }
}

private struct SceneEditorView: View {
    let extras: RemoteExtrasController
    @State var scene: TVScene
    let isNew: Bool
    let close: () -> Void

    @State private var selection: Int?
    @State private var message: String?
    @State private var confirmDelete = false
    private let tvName: String?

    init(extras: RemoteExtrasController, scene: TVScene, isNew: Bool, close: @escaping () -> Void) {
        self.extras = extras
        self._scene = State(initialValue: scene)
        self.isNew = isNew
        self.close = close
        self.tvName = scene.tv ?? extras.state.selectedDevice?.name
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(tr("Scene")).font(.system(size: 17, weight: .bold))
            Text(tr("A scene runs its steps in order: for example, it turns on the TV, opens an app and sets the volume. Run it from the “⋯” menu in the panel or from the menu-bar icon's right-click menu."))
                .font(.system(size: 11.5)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField(tr("Name"), text: $scene.name)
                .textFieldStyle(.roundedBorder)
            if let tvName, extras.state.devices.count > 1 || scene.tv != nil {
                Toggle(tr("Switch to this TV first: ") + tvName, isOn: Binding(
                    get: { scene.tv != nil }, set: { scene.tv = $0 ? tvName : nil }))
                    .toggleStyle(.checkbox)
            }
            List(selection: $selection) {
                ForEach(Array(scene.steps.enumerated()), id: \.offset) { i, st in
                    Text("\(i + 1).  " + stepText(st)).tag(i)
                }
            }
            .listStyle(.bordered(alternatesRowBackgrounds: true))
            .frame(minHeight: 170)

            HStack(spacing: 6) {
                Menu(tr("Add a step")) { addMenu }
                    .fixedSize()
                Spacer()
                Button(tr("Up")) { move(-1) }.disabled((selection ?? 0) <= 0)
                Button(tr("Down")) { move(1) }.disabled(selection == nil || selection! >= scene.steps.count - 1)
                Button(tr("Delete step")) { removeStep() }.disabled(selection == nil)
            }
            if let message {
                Text(tr(message)).font(.system(size: 11)).foregroundStyle(.red)
            }
            HStack(spacing: 6) {
                if !isNew {
                    Button(tr("Delete scene")) { confirmDelete = true }
                }
                Spacer()
                Button(tr("Run")) { if valid() { extras.run(scene) } }
                Button(tr("Cancel")) { close() }
                    .keyboardShortcut(.cancelAction)
                Button(tr("Save")) {
                    if valid() { extras.save(scene: scene); close() }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(minWidth: 420, minHeight: 440)
        .confirmationDialog(tr("Delete the scene “") + scene.name + tr("”?"), isPresented: $confirmDelete) {
            Button(tr("Delete scene"), role: .destructive) { extras.delete(scene: scene.id); close() }
        }
    }

    @ViewBuilder private var addMenu: some View {
        Button(tr("Turn on the TV")) { insert(.wake) }
        Menu(tr("Open an app")) {
            ForEach(appChoices) { app in Button(app.name) { insert(.app(app)) } }
        }
        Menu(tr("TV volume")) {
            ForEach([0, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100], id: \.self) { p in
                Button("\(p)%") { insert(.volume(percent: p)) }
            }
        }
        Menu(tr("Remote button")) {
            ForEach(TVKey.sceneKeys, id: \.id) { k in Button(tr(k.title)) { insert(.key(k.id)) } }
        }
        Menu(tr("Wait")) {
            ForEach([1, 2, 3, 5, 10, 15, 30, 60], id: \.self) { s in
                Button(tr("\(s) s")) { insert(.wait(seconds: s)) }
            }
        }
        Menu(tr("Sleep timer")) {
            ForEach(SleepTimerMath.choices, id: \.self) { m in
                Button(tr("\(m) min")) { insert(.sleepTimer(minutes: m)) }
            }
        }
        Button(tr("Turn off the TV")) { insert(.off) }
    }

    /// The TV's favourites first, then the catalog apps not among them.
    private var appChoices: [FavoriteApp] {
        var seen = Set<String>()
        return (extras.favorites + FavoriteApps.defaults).filter { seen.insert($0.id).inserted }
    }

    private func stepText(_ st: SceneStep) -> String {
        let (head, tail) = st.parts
        return tr(head) + (tail.map { tr($0) } ?? "")
    }

    private func insert(_ st: SceneStep) {
        let at = selection.map { $0 + 1 } ?? scene.steps.count
        scene.steps.insert(st, at: min(at, scene.steps.count))
        selection = min(at, scene.steps.count - 1)
        message = nil
    }

    private func move(_ d: Int) {
        guard let r = selection, scene.steps.indices.contains(r), scene.steps.indices.contains(r + d) else { return }
        scene.steps.swapAt(r, r + d)
        selection = r + d
    }

    private func removeStep() {
        guard let r = selection, scene.steps.indices.contains(r) else { return }
        scene.steps.remove(at: r)
        selection = scene.steps.isEmpty ? nil : min(r, scene.steps.count - 1)
    }

    private func valid() -> Bool {
        if scene.name.trimmingCharacters(in: .whitespaces).isEmpty { message = "Give the scene a name."; return false }
        if scene.steps.isEmpty { message = "Add at least one step."; return false }
        message = nil
        return true
    }
}
