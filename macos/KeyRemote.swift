import AppKit
import FlingKit

/// While the panel is open, the Mac keyboard drives the TV — the same keys as the
/// Windows / Linux port: arrows (hold to repeat), Return = OK, Delete = Back,
/// Home, + / − volume, Space (play/pause while casting, OK otherwise),
/// Shift+← / → seek. Not paired yet but casting: ← / → seek, Space pauses.
///
/// Works like ScrollRemote: a local monitor, only for events in the panel's
/// window, never while a text field ("Type on TV", pairing code) is being
/// edited, never with ⌘ / ⌃ / ⌥ held. Esc is left to the popover (it closes it).
///
/// Fling is a menu-bar (accessory) app, so macOS keeps the keyboard on the
/// frontmost app even with the panel open. The app is activated while the
/// panel is shown and handed back when it closes.
@MainActor
final class KeyRemote {
    private let state: AppState
    private let isTarget: (NSEvent) -> Bool
    private var monitor: Any?
    private var observers: [NSObjectProtocol] = []
    private var activatedByUs = false

    // Virtual key codes (layout-independent, so a Russian layout works the same)
    private enum K {
        static let left: UInt16 = 123, right: UInt16 = 124, down: UInt16 = 125, up: UInt16 = 126
        static let ret: UInt16 = 36, enter: UInt16 = 76, delete: UInt16 = 51, fwdDelete: UInt16 = 117
        static let space: UInt16 = 49, home: UInt16 = 115
        static let equal: UInt16 = 24, minus: UInt16 = 27, padPlus: UInt16 = 69, padMinus: UInt16 = 78
    }
    private static let repeatable: Set<UInt16> = [K.left, K.right, K.up, K.down,
                                                  K.equal, K.minus, K.padPlus, K.padMinus]
    private static let seekStep = 30

    init(state: AppState, isTarget: @escaping (NSEvent) -> Bool) {
        self.state = state
        self.isTarget = isTarget
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, self.isTarget(event), self.handle(event) else { return event }
            return nil   // consumed: the TV got it
        }
        let nc = NotificationCenter.default
        observers.append(nc.addObserver(forName: NSPopover.didShowNotification, object: nil,
                                        queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.panelShown() }
        })
        observers.append(nc.addObserver(forName: NSPopover.didCloseNotification, object: nil,
                                        queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.panelClosed() }
        })
    }

    deinit {
        if let monitor { NSEvent.removeMonitor(monitor) }
        for o in observers { NotificationCenter.default.removeObserver(o) }
    }

    private func panelShown() {
        guard !NSApp.isActive else { return }
        activatedByUs = true
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Give the keyboard back to the app the user was in, unless a real Fling
    /// window (onboarding, …) is still open.
    private func panelClosed() {
        guard activatedByUs else { return }
        activatedByUs = false
        let otherWindows = NSApp.windows.contains {
            $0.isVisible && $0.styleMask.contains(.titled) && !$0.className.contains("Popover")
        }
        if !otherWindows { NSApp.deactivate() }
    }

    private func handle(_ event: NSEvent) -> Bool {
        if event.modifierFlags.intersection([.command, .control, .option]) != [] { return false }
        if event.window?.firstResponder is NSText { return false }   // typing in a field
        let key = event.keyCode
        if event.isARepeat && !Self.repeatable.contains(key) { return true }   // swallow, don't spam
        let casting = state.panel == .casting
        let shift = event.modifierFlags.contains(.shift)

        if casting && shift && (key == K.left || key == K.right) {
            if !event.isARepeat { seek(key == K.right) }
            return true
        }
        if state.tvPaired {
            let code: Int32
            switch key {
            case K.up: code = ATVKeyCode.dpadUp
            case K.down: code = ATVKeyCode.dpadDown
            case K.left: code = ATVKeyCode.dpadLeft
            case K.right: code = ATVKeyCode.dpadRight
            case K.ret, K.enter: code = ATVKeyCode.dpadCenter
            case K.delete, K.fwdDelete: code = ATVKeyCode.back
            case K.home: code = ATVKeyCode.home
            case K.equal, K.padPlus: code = ATVKeyCode.volumeUp
            case K.minus, K.padMinus: code = ATVKeyCode.volumeDown
            case K.space:
                if casting {
                    Task { await state.togglePlayPause() }
                    return true
                }
                code = ATVKeyCode.dpadCenter
            default: return false
            }
            RemoteKeyFlash.post(code)
            Task { await state.pressTVKey(code) }
            return true
        }
        if casting {   // no remote yet — keep cast transport on the keys
            switch key {
            case K.left, K.right: seek(key == K.right); return true
            case K.space: Task { await state.togglePlayPause() }; return true
            default: return false
            }
        }
        return false
    }

    private func seek(_ forward: Bool) {
        let s = forward ? Self.seekStep : -Self.seekStep
        Task { await state.seek(by: s) }
    }
}

/// Lights up the matching on-screen key for a moment when it was pressed on the keyboard.
enum RemoteKeyFlash {
    static let name = Notification.Name("FlingRemoteKeyFlash")
    static func post(_ code: Int32) {
        NotificationCenter.default.post(name: name, object: nil, userInfo: ["code": code])
    }
}
