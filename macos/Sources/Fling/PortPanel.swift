// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import SwiftUI
import FlingKit

/// Port: the panel rows the Windows build has and upstream doesn't — the tab another
/// browser's extension reported, files / slideshow, the queue strip and the extension hint.
/// PanelView places it with one line in the idle and the casting section.
struct PortPanelRows: View {
    @ObservedObject var state: AppState
    var casting = false
    @ObservedObject private var port = PortServices.shared
    @ObservedObject private var queue = PortServices.shared.queue
    @ObservedObject private var settings = PortSettings.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if queue.isLive || queue.phase == .stopped && queue.count > 0 { queueStrip }
            if !casting, settings.showTabs, let tab = otherBrowserTab {
                MenuRow(title: String(format: tr("Cast from %@: %@"), tab.browser, String(tab.title.prefix(40))),
                        icon: "globe", enabled: !state.castInFlight) {
                    Task {
                        await port.castLink(tab.url, title: tab.title,
                                            start: tab.resumeAt ?? MediaFiles.urlStart(tab.url), mediaSrc: tab.mediaSrc)
                    }
                }
            }
            if !casting {
                MenuRow(title: "Files from this Mac to the TV…", icon: "folder") { port.pickFiles() }
                MenuRow(title: "Photo slideshow…", icon: "photo.on.rectangle") { port.pickFiles(slideshow: true) }
            }
            if !casting, settings.showTabs, settings.extensionHint, !settings.extensionSeen,
               Self.otherBrowserInstalled { extensionHint }
        }
    }

    /// Browsers upstream can't read over AppleScript: the extension hint is for them only.
    static let otherBrowserInstalled: Bool = [
        "org.mozilla.firefox", "com.microsoft.edgemac", "com.brave.Browser", "company.thebrowser.Browser",
        "com.operasoftware.Opera", "com.vivaldi.Vivaldi", "ru.yandex.desktop.yandex-browser",
    ].contains { NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) != nil }

    /// A fresh report from an extension in a browser upstream can't read (Firefox, Edge,
    /// Brave…), or one whose page differs from the tab the panel already shows.
    private var otherBrowserTab: PortServices.BrowserTab? {
        guard let t = port.browserTab, t.fresh else { return nil }
        if let shown = state.tab, shown.url == t.url { return nil }
        if Browser.allCases.contains(where: { $0.displayName.caseInsensitiveCompare(t.browser) == .orderedSame }) {
            return nil                                      // Chrome / Safari: the panel reads those itself
        }
        return t
    }

    private var queueStrip: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Image(systemName: queue.isSlideshow ? "photo.on.rectangle" : "list.bullet")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                Text(queue.positionLabel).font(.system(size: 11).monospacedDigit()).foregroundStyle(.secondary)
                Text(queue.current?.name ?? "").font(.system(size: 11.5)).lineLimit(1)
                Spacer(minLength: 4)
                button("backward.end.fill", tr("Previous")) { queue.previous() }
                if queue.isSlideshow {
                    button(queue.slidePaused ? "play.fill" : "pause.fill",
                           tr(queue.slidePaused ? "Resume slideshow" : "Pause slideshow")) { queue.toggleSlideshowPause() }
                } else if queue.phase == .stopped {
                    button("play.fill", tr("Resume the queue")) { queue.resumeQueue() }
                }
                button("forward.end.fill", tr("Next")) { queue.next() }
                button("xmark", tr("Clear the queue")) { queue.stop() }
            }
            if queue.phase == .stopped {
                Text(tr("The queue is waiting — press ▶ to carry on."))
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 5)
    }

    private func button(_ icon: String, _ help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: icon).font(.system(size: 10.5)) }
            .buttonStyle(.borderless).help(help)
    }

    private var extensionHint: some View {
        HStack(alignment: .top, spacing: 6) {
            Text(tr("Firefox, Edge, Brave…: add the Fling browser extension to cast their tabs, from where the video is."))
                .font(.system(size: 10)).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 2)
            Button(tr("How")) { PortServices.shared.openSettings() }.buttonStyle(.link).font(.system(size: 10))
            Button(tr("Hide")) { settings.extensionHint = false }.buttonStyle(.link).font(.system(size: 10))
        }
        .padding(.horizontal, 14).padding(.vertical, 4)
    }
}

/// Port: right-click menu items, added by ContextMenuBuilder.
@MainActor
enum PortMenu {
    static func items(_ make: (String, String, NSEvent.ModifierFlags, Bool, @escaping () -> Void) -> NSMenuItem)
        -> [NSMenuItem] {
        let port = PortServices.shared, q = port.queue
        var out = [
            make("Files from this Mac to the TV…", "", [], true) { port.pickFiles() },
            make("Photo slideshow…", "", [], true) { port.pickFiles(slideshow: true) },
        ]
        if q.count > 1 {
            out.append(make("Next in queue", "", [], true) { q.next() })
            out.append(make("Previous in queue", "", [], true) { q.previous() })
            out.append(make("Clear the queue", "", [], true) { q.stop() })
        }
        let n = make("Notify when the video changes", "", [], true) { port.settings.notifyTrack.toggle() }
        n.state = port.settings.notifyTrack ? .on : .off
        out.append(n)
        out.append(make("Settings…", ",", [.command], true) { port.openSettings() })
        return out
    }
}
