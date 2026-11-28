// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation
import Combine

/// Port: the app settings of the Windows / Linux build (fling.json) that make sense on the
/// Mac, kept in UserDefaults. Observable so the panel and the Settings window stay in step.
@MainActor
public final class PortSettings: ObservableObject {
    public static let shared = PortSettings()

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        defaults.register(defaults: [
            Key.notifyTrack: true, Key.showTabs: true, Key.bridge: true, Key.hotkey: true,
            Key.resume: true, Key.slideSeconds: 7, Key.repeatMode: RepeatMode.off.rawValue,
            Key.shuffle: false, Key.extensionSeen: false, Key.extensionHint: true,
        ])
    }

    enum Key {
        static let notifyTrack = "notify_track", showTabs = "show_tabs", bridge = "bridge"
        static let hotkey = "global_hotkey", resume = "resume", slideSeconds = "slideshow_sec"
        static let repeatMode = "repeat", shuffle = "shuffle", extensionSeen = "ext_seen"
        static let extensionHint = "ext_hint", mediaDir = "media_dir", positions = "resume_positions"
    }

    public enum RepeatMode: String, CaseIterable, Sendable { case off, all, one }

    private func flag(_ k: String) -> Bool { defaults.bool(forKey: k) }
    private func set(_ v: Any?, _ k: String) { objectWillChange.send(); defaults.set(v, forKey: k) }

    /// Notification when the TV moves to another video / track.
    public var notifyTrack: Bool { get { flag(Key.notifyTrack) } set { set(newValue, Key.notifyTrack) } }
    /// "Casting browser tabs": the tab block and "Cast this tab" in the panel.
    public var showTabs: Bool { get { flag(Key.showTabs) } set { set(newValue, Key.showTabs) } }
    /// Local bridge for the browser extension (127.0.0.1:47613). Takes effect on restart.
    public var bridge: Bool { get { flag(Key.bridge) } set { set(newValue, Key.bridge) } }
    /// ⌃⌥⌘C system-wide, no Accessibility permission needed.
    public var hotkey: Bool { get { flag(Key.hotkey) } set { set(newValue, Key.hotkey) } }
    public var resume: Bool { get { flag(Key.resume) } set { set(newValue, Key.resume) } }
    public var shuffle: Bool { get { flag(Key.shuffle) } set { set(newValue, Key.shuffle) } }
    public var slideSeconds: Int {
        get { max(2, defaults.integer(forKey: Key.slideSeconds)) }
        set { set(max(2, newValue), Key.slideSeconds) }
    }
    public var repeatMode: RepeatMode {
        get { RepeatMode(rawValue: defaults.string(forKey: Key.repeatMode) ?? "") ?? .off }
        set { set(newValue.rawValue, Key.repeatMode) }
    }
    /// An extension has talked to the app at least once.
    public var extensionSeen: Bool { get { flag(Key.extensionSeen) } set { set(newValue, Key.extensionSeen) } }
    /// The "install the browser extension" hint in the panel (Hide turns it off).
    public var extensionHint: Bool { get { flag(Key.extensionHint) } set { set(newValue, Key.extensionHint) } }
    public var mediaDir: String? { get { defaults.string(forKey: Key.mediaDir) } set { set(newValue, Key.mediaDir) } }

    // MARK: - resume positions (things of 10+ minutes)

    public static let resumeKeep = 150

    public func resumePosition(_ key: String) -> TimeInterval? {
        guard resume, let all = defaults.dictionary(forKey: Key.positions) as? [String: [Double]],
              let e = all[key], e.count >= 1 else { return nil }
        return e[0] > 5 ? e[0] : nil
    }

    /// Stores where `key` is; near the end (watched) the bookmark is dropped.
    public func storePosition(_ key: String, position: TimeInterval?, duration: TimeInterval?) {
        guard let position, let duration, duration >= MediaFiles.resumeMinDuration else { return }
        var all = (defaults.dictionary(forKey: Key.positions) as? [String: [Double]]) ?? [:]
        if position >= duration - max(25, duration * 0.03) || position < 5 {
            all[key] = nil
        } else {
            all[key] = [position, Date().timeIntervalSince1970]
            if all.count > Self.resumeKeep {           // forget the oldest
                for k in all.sorted(by: { ($0.value.last ?? 0) < ($1.value.last ?? 0) })
                    .prefix(all.count - Self.resumeKeep).map(\.key) { all[k] = nil }
            }
        }
        defaults.set(all, forKey: Key.positions)       // no objectWillChange: nothing shows it
    }
}
