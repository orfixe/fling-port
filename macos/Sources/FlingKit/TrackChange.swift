// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import Foundation

/// Port (fling_app.py `_track_changed`): notices when the TV moves on to another video /
/// track. Fed every status poll; answers the new title only on a real change — not the
/// first sight, not the same title again, not another TV, not a generic "YouTube".
/// A short gap with nothing playing (the TV loading the next item) keeps the last title;
/// after `gap` seconds of silence the next title counts as a first sight again.
public struct TrackChangeDetector: Sendable {
    private var last: (device: String, title: String, seen: Date)?
    public var gap: TimeInterval = 30
    static let generic: Set<String> = ["YouTube", "Playing", "Default Media Receiver", "Backdrop"]

    public init() {}

    public mutating func feed(device: String?, status: CastStatus, now: Date = Date()) -> String? {
        guard let device else { last = nil; return nil }
        if let l = last, l.device != device || now.timeIntervalSince(l.seen) > gap { last = nil }
        guard status.hasMedia,
              let title = status.title?.trimmingCharacters(in: .whitespacesAndNewlines),
              !title.isEmpty, !Self.generic.contains(title)
        else { return nil }
        defer { last = (device, title, now) }
        guard let prev = last, prev.title != title else { return nil }
        return title
    }
}
