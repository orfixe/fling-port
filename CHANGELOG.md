# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is an unofficial port of [Fling](https://github.com/tatianathevisionary/fling)
by Damion Rashford and Tatiana; see [CREDITS.md](CREDITS.md).

## [Unreleased]

### Added

- Desktop: "Appearance" in the tray menu — dark, light or as the system (follows a system
  switch once no Fling window is open), and the accent colour: system or a preset. A clearer
  keyboard icon for typing on the TV.
- Desktop: "TV screen with mouse" — the TV picture in a window, and the computer mouse becomes a
  real pointer on the TV (scrcpy, downloaded on first use and checked against its SHA256 sums).
  "Mouse for the TV" does the same without the picture. A click on the artwork opens the screen.
- Desktop: ADB console — run commands on the selected TV with live output, quick commands,
  history and Stop; commands that may delete data or restart the TV ask first.
- Desktop: `fling screen`, `fling mouse`, `fling adb`, `fling log`, `fling install-extension`.
- Windows: "Install the extension…" adds the extension to Chrome or Edge by itself, with the
  manual steps as a fallback.

## [0.3.2] - 2026-11-23

Includes the macOS update below (Russian / English, WASD, TV volume slider, no keychain prompts).

### Fixed

- Android: the app crashed on Android 8–12 when adding a TV by IP, sharing a YouTube link or
  looking up what's playing over ADB.
- Android: no crash when Back or a dark-mode switch lands during a TV status update.
- Desktop: key presses right after the TV wakes no longer fail with "TV closed the connection" —
  one TV-remote connection at a time.
- Desktop: a locked `fling.json` or an error in a button handler no longer closes Fling; Add TV
  by IP rejects digits such as "²" instead of crashing.
- Desktop (Windows): Cyrillic titles and TV names from catt are no longer garbled.
- Windows: the `fling` command works for user folders with spaces and when run with no arguments.

### Security

- Desktop: the browser bridge only casts web (http/https) links, not local or network files;
  artwork is fetched over http/https only; seek requests are bounded.
- Android: TV keys and the paired-remote certificate are kept out of backups; oversized
  remote-protocol frames are rejected.

### Changed

- Credit line names orfixe as the porter in the apps, the extension, the installer and macOS.

## macOS update - 2026-11-23

The macOS files in the [0.3.1] release were replaced; Windows, Android and the extensions are
unchanged.

### Added

- macOS: Russian / English like the Windows build — follows the system language; *Language* row in
  the panel and the right-click menu switches it and relaunches Fling.
- macOS: the keyboard button types on the TV live (letters appear as you type, Return runs the
  search) or sends on Return (right-click to choose); paste the clipboard on the TV with the
  clipboard button or ⌘V.
- macOS: W A S D as an alternative to the arrow keys (E = OK, Q = Back), picked with a small
  arrows / WASD switch under the d-pad; a compact key guide below it follows the switch.
- macOS: TV volume slider from the level the TV reports (from the Windows build) — percent of the
  TV's own scale, a bubble while dragging, the speaker mutes; TVs that don't report volume keep the
  − / mute / + keys.
- macOS: the small ⌨ badge beside the TV power key turns keyboard capture on / off (on by default).

### Changed

- macOS: built on the author's current Fling with the author's d-pad layout (the round pad is gone);
  the bright "keyboard captured" strip is now a quiet badge.

### Fixed

- macOS: Fling kept asking for access to a key in the keychain ("Always Allow" didn't stick). The
  TV remote's identity now stays in memory, loaded from Fling's own file — no keychain, no prompts.
  Pairing is kept.
- macOS: the pairing code field only took the English layout; the Russian one works now
  (ф и с в у а → A–F), as on Windows.
- macOS: the build failed with Command Line Tools on macOS 27 (SwiftUI macro plugin missing); it
  now falls back to the macOS 26 SDK and finds the binary in either build folder.
- macOS: signed with a local "Fling Port Local" identity when the Mac has one, so the signature no
  longer changes with every build.

## [0.3.1] - 2026-11-22

### Added

- Desktop: install an APK on the TV over ADB — Android badge → *Install APK…*, the same button in
  Connect TV, `fling install-apk app.apk`, or right-click an `.apk` in Explorer → *Install on TV (Fling)*
  (Windows). Several files at once install one split app. Clear messages for the usual failures
  (other signature, newer version installed, wrong processor, no space).
- Android: Fling checks for a newer release (quietly once a day, or *Version … · Check for updates*
  at the bottom), downloads `Fling.apk` and opens Android's own install screen; it goes over the
  old app, pairing and settings stay. While the repository is private, GitHub needs your own
  read-only token, which Fling asks for once and keeps only on the phone.
- Windows: a real setup, `Fling-Setup-<version>.exe` (Inno Setup): per-user install without admin
  rights, Start menu, optional desktop icon and autostart, the `.apk` menu and the `fling` command,
  uninstall from Settings → Apps.

### Changed

- Desktop: the d-pad is a round pad like on a TV remote — the four arrows sit at the same
  distance from OK, the whole quarter of the ring presses that arrow (hold to repeat), and
  Back / Home sit below its sides.
- Desktop: a strip at the top of the panel says when the keyboard is captured for the TV
  (Esc releases it); it hides while a text field is open.
- Windows (installed from source): Fling runs as `Fling.exe` with its own name and icon, so Task
  Manager lists "Fling" as one process instead of two "Python" ones.
- macOS: the same round d-pad and "keyboard captured" strip; Esc closes the panel.
- Desktop: TV volume as a slider with the TV's own level (the TV reports it over the remote
  session, no polling); the speaker icon mutes. TVs that don't report a volume keep the
  − / mute / + keys. The laptop's own volume keys no longer drive the TV.
- Desktop: the volume shows in percent; while you drag, a bubble above the slider shows the
  level. Works on TVs whose scale isn't 0–100.
- Desktop: shorter button labels ("Settings", "Reboot", "Turn on" instead of "… TV"), and the
  keyboard hint under the d-pad is gone — each button's tooltip names its key.

### Fixed

- Windows installer: Fling is stopped before catt is updated. Updating catt while Fling was
  running could fail and leave casting without catt; a rerun now repairs that.

## [0.3.0] - 2026-11-22

### Added

- Desktop: the laptop keyboard works as a TV remote — arrow keys (hold to scroll through menus),
  WASD, Enter, Backspace, numpad and media keys; WASD also works on a Russian layout.
  On Windows this works while the panel is open, even though Windows keeps the keyboard on
  the browser: arrows, Enter = OK, Backspace = Back, Home, +/− volume, Space, Esc closes the
  panel, Shift+←/→ seeks. The matching button in the panel lights up. Ctrl / Alt / Win
  shortcuts are left alone, and a text field gets the keyboard while it is open.
- Desktop: the keyboard icon opens a field for typing on the TV.
- Desktop: an Android badge shows whether ADB is connected. Once the TV allows debugging,
  Connect TV offers everyday tools: wake, sleep, home, screenshot, settings, apps,
  force-stop, reboot, disconnect. The Android button in the panel opens the same menu.
- macOS: a private build of Fling with the keyboard icon, installed from Terminal
  (`macos/install.sh`) so macOS doesn't quarantine it.
- macOS: the Mac keyboard drives the TV while the panel is open — arrows, Return = OK,
  Delete = Back, Home, +/− volume, Space, Shift+←/→ seeks; the matching key lights up.

### Fixed

- The TV no longer gets a flood of "Allow debugging?" prompts: Fling waits before retrying,
  doesn't connect by itself when Connect TV opens, and uses IPv4 only.
- Connect TV accepts a typed IP and a wireless-debugging port other than 5555 (or `IP:port`).
- If `adb` is missing, Connect TV shows where to download platform-tools.
- Fewer windows on top of each other: help doesn't open by itself, and Connect TV and the guide
  don't both stay open.

### Changed

- Connect TV and the extension options no longer show the YouTube-code and Bluetooth rows or the
  unused notification / port settings. Notifications and start-at-login stay in the tray menu.

## [0.2.0] - 2026-11-21

### Added

- **Windows port** (Windows 10 1809+ / 11, x64; ARM64 through emulation): the same tray panel,
  Connect TV window, remote, voice search and guide as on Linux. A standalone `Fling.exe`
  (catt and yt-dlp bundled) or an install from source with `windows\install.cmd`, both without
  admin rights. Start menu entry, `fling` command, start at login, Windows notifications.
- Windows: system-wide **Win+Shift+C**; if another app owns it, Ctrl+Alt+Shift+C, and the panel
  shows which one works.
- **Browser extension** for Chrome, Edge, Brave, Opera, Vivaldi, Yandex, Firefox and Safari,
  built from one source (`extension/build.py`): a popup remote, *Cast this tab* that resumes at
  the page's position, the page's own stream for sites yt-dlp can't open, *Cast link / video /
  page* in the right-click menu, Alt+Shift+C, pausing the page after casting, English and Russian.
- A local bridge on `127.0.0.1:47613` between the extension and the app
  (`desktop/fling_bridge.py`). It accepts browser extensions and local tools only (Host and
  Origin checks, a required `X-Fling` header). With it the panel's *Cast this tab* works with
  any browser, not just Firefox.
- A notification when the TV moves to another video or track. The bell in the panel, the tray
  menu and the extension settings turn it on (green) or off (red).
- Panel: a compact view (artwork and playback keys, labels in tooltips), a **?** button for the
  guide, a small language flag at the bottom, icons for every row, clearer labels
  (*Pair with TV* with a green TV-plus icon, *Cast copied link*, *Take screenshot*, *Select* next to the TV's name). The Connect TV window, the TV menu and screenshot messages are now translated into English too.
- Android: short tips on first launch, one at a time over the real screen (a spotlight and a
  small card with the system's Material motion) instead of a long help page; the full guide is
  one tap away. Choose between several TVs, add or remove TVs by IP.
- Android: `Fling.apk` 0.2.0 (version code 2). It is signed with a new key, so a phone with
  0.1.0 has to uninstall the old app first and pair the TV remote again.
- `android/build.sh` also works on Windows (Git Bash) and creates the debug key when missing.
- Linux: choose between several TVs from the panel footer, the tray menu and the Connect TV
  window; find TVs again; add a TV by IP address; identical names are told apart by address;
  paired TVs are marked. Built-in "How to use" guide (`fling help`, panel, tray menu).
- Step-by-step Windows guide in English and Russian (`docs/install-windows*.md`) and a Russian
  README.

### Fixed

- A TV added by IP now has to be a real address (each number 0–255). Asking the TV for its name closes the connection afterwards. On Android, removing the only TV also disconnects from it.

### Changed

- The desktop app moved to `desktop/` and is shared by Linux and Windows; every OS-specific
  call goes through `desktop/fling_platform.py`. Linux behaviour, paths and pairing are unchanged.
- Voice search on Windows records through `sounddevice` and resamples any device format
  (44.1/48 kHz, stereo or 4-channel array mics) to the TV's 8 kHz mono.
- The panel drops its transparent rounded corners in Remote Desktop sessions and without
  desktop composition (`"solid_panel"` in `fling.json` overrides).
- Missing English texts in the panel (TV screenshot, How to use, Connect TV, the footer).
- Linux: on battery, no background polling while the panel is closed; the
  Bluetooth remote service starts only when you ask for it.
- Linux: switching TVs resets now-playing and remote state for the new TV.
- Linux: background polling every 10 s instead of 2 s, using the cheap ADB read;
  `catt` is only polled for a live Cast session ADB can't see (≈10% of a CPU
  core saved while the panel is closed).

## [0.1.0] - 2026-11-21

### Added

#### Linux (KDE Plasma)

- Tray icon with a popover-style panel modelled on the macOS Fling panel:
  artwork, title, progress, play/pause, ±30 s, a debounced volume slider,
  stop, and a device footer with a switcher.
- Casting of the current Firefox tab (read from the session store) and of
  clipboard links through [catt](https://github.com/skorokithakis/catt);
  global **Meta+Shift+C** shortcut casts the current tab.
- Real now-playing: title, artist, position and play state read from the TV
  over ADB (`dumpsys media_session`), with artwork and duration looked up
  on YouTube.
- Optional YouTube "Link with TV code" for the exact video and position in
  the YouTube TV app, plus next / previous video.
- Detection of the frozen Cast session that native YouTube on Google TV
  reports: stale artwork is hidden and pause / seek are sent as remote keys.
- TV remote over the Android TV Remote v2 protocol: one-time code pairing
  (on-screen 0–9 / A–F keys, Cyrillic layout auto-mapped), power, app
  launcher, d-pad with Back / Home, volume, mute, typing and voice search;
  scrolling over the panel moves the d-pad.
- The laptop as a **Bluetooth LE remote** (HID over GATT, "Fling Remote")
  for when the network remote isn't paired.
- **Connect TV** window: one button finds the TV on the local network,
  pairs the remote, connects ADB and shows what still needs a tap on the TV.
- TV screenshots over ADB, saved to `~/Pictures/Fling`; black DRM-protected
  frames are detected and explained.
- English / Russian interface, switchable from the panel.
- Terminal remote `fling-tui`, installer / uninstaller `install.sh`.

#### Android

- Plain-Java app (Android 8.0+, no Google Play Services, no Gradle) with the
  same panel: now playing, link card, TV remote, device footer.
- Share a link to Fling and it plays on the TV; the last five links can be
  re-sent with one tap.
- Built-in Cast v2, Android TV Remote v2 and ADB clients: real now-playing
  and artwork straight from the TV, screenshots to `Pictures/Fling`.
- Phone volume keys control the TV while the app is open; touchpad mode
  (swipe = d-pad, tap = OK, long press = Back); Quick Settings tile
  "Pause on TV".
- English / Russian interface (RU | EN switch, follows the system by default).

#### Both

- Authorship line "Fling by Damion Rashford & Tatiana · unofficial port by orfixe",
  linking to the original repository.

### Security

- Release clean-up: the APK's manual-IP hint now shows an example address
  instead of a real one, and the TV's name is hidden in the screenshots.

[unreleased]: https://github.com/orfixe/fling-port/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/orfixe/fling-port/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/orfixe/fling-port/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/orfixe/fling-port/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/orfixe/fling-port/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/orfixe/fling-port/releases/tag/v0.1.0
