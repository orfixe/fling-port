// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import UserNotifications

/// Port: the Windows / Linux toasts ("Now on TV: …", queue messages) as macOS notifications.
/// Permission is asked the first time something is posted.
@MainActor
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    static let shared = Notifier()

    /// UNUserNotificationCenter traps without a bundle (`swift run`, tests).
    private var center: UNUserNotificationCenter? {
        Bundle.main.bundleIdentifier == nil ? nil : UNUserNotificationCenter.current()
    }
    private var asked = false

    func post(_ body: String) {
        guard let center else { NSLog("Fling: %@", body); return }
        center.delegate = self
        let send = {
            let c = UNMutableNotificationContent()
            c.title = "Fling"
            c.body = body
            center.add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
        }
        if asked { send(); return }
        asked = true
        center.requestAuthorization(options: [.alert, .sound]) { ok, _ in if ok { send() } }
    }

    /// Fling is a menu-bar app, always "active": show banners anyway.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .list])
    }
}
