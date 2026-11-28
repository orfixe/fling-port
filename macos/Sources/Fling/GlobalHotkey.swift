// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import Carbon.HIToolbox

/// Port: the Windows build's Win+Shift+C as ⌃⌥⌘C — "cast the current tab" from anywhere.
///
/// Upstream's ⌘⇧C is a passive NSEvent monitor: it needs Accessibility access and leaves
/// the keystroke to the frontmost app (⌘⇧C means something in Finder, Chrome DevTools,
/// many editors). A Carbon hot key needs no permission, and ⌃⌥⌘C is free in macOS and the
/// common apps. Both stay: ⌘⇧C keeps working for those who granted Accessibility.
@MainActor
final class GlobalHotkey {
    static let label = "⌃⌥⌘C"

    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private static var action: (() -> Void)?

    init(action: @escaping () -> Void) {
        Self.action = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { GlobalHotkey.action?() }
            return noErr
        }, 1, &spec, nil, &handler)
        let id = EventHotKeyID(signature: OSType(0x464C_4E47), id: 1)      // "FLNG"
        let status = RegisterEventHotKey(UInt32(kVK_ANSI_C), UInt32(controlKey | optionKey | cmdKey), id,
                                         GetApplicationEventTarget(), 0, &ref)
        if status != noErr { NSLog("Fling: hotkey ⌃⌥⌘C is taken (%d)", status) }
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handler { RemoveEventHandler(handler) }
    }
}
