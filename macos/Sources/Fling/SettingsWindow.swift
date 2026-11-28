// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import SwiftUI
import FlingKit

/// Port: Settings (fling_settings.py) — General, Casting, Files & queue. The Windows /
/// Linux themes are left out on purpose: this build is native AppKit / SwiftUI and follows
/// the system's light / dark appearance and accent colour, like every Mac app.
enum SettingsWindow {
    @MainActor static func make() -> NSWindow {
        let host = NSHostingController(rootView: SettingsView())
        let w = NSWindow(contentViewController: host)
        w.title = tr("Fling Settings")
        w.styleMask = [.titled, .closable, .miniaturizable]
        w.isReleasedWhenClosed = false
        w.center()
        return w
    }
}

struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettings().tabItem { Label(tr("General"), systemImage: "gearshape") }
            CastingSettings().tabItem { Label(tr("Casting"), systemImage: "play.tv") }
            QueueSettings().tabItem { Label(tr("Files & queue"), systemImage: "list.bullet") }
        }
        .padding(16)
        .frame(width: 520)
    }
}

/// Small grey explanation under a control.
private struct Note: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(tr(text)).font(.system(size: 11)).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private struct GeneralSettings: View {
    @ObservedObject private var s = PortSettings.shared
    @State private var login = LoginItem.isEnabled
    @State private var lang = UserDefaults.standard.string(forKey: "lang") ?? "system"

    var body: some View {
        Form {
            Section {
                Picker(tr("Language"), selection: $lang) {
                    Text(tr("As the system")).tag("system")
                    Text("English").tag("en")
                    Text("Русский").tag("ru")
                }
                .onChange(of: lang) { _, v in Lang.set(v == "system" ? nil : v) }
                Note("Fling restarts in the chosen language.")
            }
            Section {
                Toggle(tr("Open at Login"), isOn: $login)
                    .onChange(of: login) { _, v in if v != LoginItem.isEnabled { LoginItem.toggle() } }
                Toggle(tr("Casting browser tabs"), isOn: $s.showTabs)
                Note("The tab, “Cast this tab” and the browser extension hint in the panel.")
                Toggle(tr("Browser extension hint"), isOn: $s.extensionHint).disabled(!s.showTabs)
            }
            Section {
                Toggle(tr("Notify when the video changes"), isOn: $s.notifyTrack)
                Note("When the TV starts another video or track and the panel is closed.")
            }
        }
        .formStyle(.grouped)
    }
}

private struct CastingSettings: View {
    @ObservedObject private var s = PortSettings.shared
    @ObservedObject private var port = PortServices.shared

    var body: some View {
        Form {
            Section {
                Toggle(isOn: Binding(get: { s.hotkey }, set: { s.hotkey = $0; port.setHotkey($0) })) {
                    HStack {
                        Text(tr("Cast the current tab"))
                        Spacer()
                        Text(GlobalHotkey.label).font(.system(size: 12, weight: .semibold))
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(RoundedRectangle(cornerRadius: 5).fill(Color.primary.opacity(0.08)))
                    }
                }
                Note("Works everywhere, even with the panel closed. Chrome and Safari are read directly; other browsers through the Fling extension. ⌘⇧C works too once Fling has Accessibility access.")
            }
            Section {
                Toggle(tr("Link with the browser extension"), isOn: $s.bridge)
                Note("The extension tells Fling which tab is open, casts it from where the page's video is, and adds a Fling button to the browser. Takes effect after Fling restarts.")
                if let err = port.bridgeError {
                    Text(String(format: tr("The bridge couldn't start: %@"), err))
                        .font(.system(size: 11)).foregroundStyle(.orange)
                } else if s.bridge {
                    Note(String(format: tr("Listening on 127.0.0.1:%d — this Mac only."), Int(port.bridgePort)))
                }
            }
            Section(tr("Install the extension")) {
                Note("Chrome, Edge, Brave, Opera, Vivaldi: open the browser's extensions page, turn on Developer mode, press Load unpacked and choose the “chrome” folder.")
                Note("Firefox: about:debugging → This Firefox → Load Temporary Add-on… → manifest.json in the “firefox” folder (until Firefox restarts).")
                Note("Safari: build it with extension/safari/build-safari.sh on a Mac with Xcode.")
                HStack {
                    Button(tr("Show extension folder")) { ExtensionFiles.reveal() }
                        .disabled(ExtensionFiles.folder == nil)
                    Button(tr("Open Chrome extensions")) { ExtensionFiles.openPage("Google Chrome", "chrome://extensions") }
                    Button(tr("Open Edge extensions")) { ExtensionFiles.openPage("Microsoft Edge", "edge://extensions") }
                }
                if s.extensionSeen { Note("✓ An extension has connected to Fling.") }
            }
        }
        .formStyle(.grouped)
    }
}

private struct QueueSettings: View {
    @ObservedObject private var s = PortSettings.shared

    var body: some View {
        Form {
            Section {
                Picker(tr("Repeat"), selection: $s.repeatMode) {
                    Text(tr("Off")).tag(PortSettings.RepeatMode.off)
                    Text(tr("The whole queue")).tag(PortSettings.RepeatMode.all)
                    Text(tr("One item")).tag(PortSettings.RepeatMode.one)
                }
                Toggle(tr("Shuffle"), isOn: $s.shuffle)
                Picker(tr("Slideshow: next photo after"), selection: $s.slideSeconds) {
                    ForEach([3, 5, 7, 10, 15, 30, 60], id: \.self) { Text(String(format: tr("%d s"), $0)).tag($0) }
                }
                Toggle(tr("Resume where you left off"), isOn: $s.resume)
                Note("Films and episodes of 10 minutes or more continue from the same place next time.")
            }
            Section {
                Note("Files from this Mac: videos (mp4, m4v, mov, webm, mkv), music, photos (heic, webp and tiff are converted), m3u / pls playlists and whole folders. Subtitles next to a video (film.srt, film.en.srt, Subs/…) are picked up automatically; srt and ass are converted for the TV.")
            }
        }
        .formStyle(.grouped)
    }
}

/// The unpacked extension shipped inside Fling.app (build.sh copies extension/dist/{chrome,firefox}).
enum ExtensionFiles {
    static var folder: URL? {
        guard let u = Bundle.main.resourceURL?.appendingPathComponent("extension"),
              FileManager.default.fileExists(atPath: u.path) else { return nil }
        return u
    }

    /// The browser can't load from inside an app bundle that updates replace, so the
    /// folders are copied to ~/Library/Application Support/Fling Port/extension first.
    static func reveal() {
        guard let src = folder else { return }
        let fm = FileManager.default
        let dest = fm.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Fling Port/extension")
        try? fm.removeItem(at: dest)
        try? fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
        do { try fm.copyItem(at: src, to: dest) } catch { NSWorkspace.shared.activateFileViewerSelecting([src]); return }
        NSWorkspace.shared.activateFileViewerSelecting([dest.appendingPathComponent("chrome")])
    }

    static func openPage(_ app: String, _ url: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-a", app, url]
        try? p.run()
    }
}
