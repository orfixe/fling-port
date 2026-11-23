# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is an unofficial port of [Fling](https://github.com/tatianathevisionary/fling)
by Damion Rashford and Tatiana; see [CREDITS.md](CREDITS.md).

## [Unreleased]

### Added

- Desktop: install an APK on the TV over ADB — Android badge → *Install APK…*, the same button in
  Connect TV, `fling install-apk app.apk`, or right-click an `.apk` in Explorer → *Install on TV (Fling)*
  (Windows). Several files at once install one split app. Clear messages for the usual failures
  (other signature, newer version installed, wrong processor, no space).

### Changed

- Desktop: the d-pad is a round pad like on a TV remote — the four arrows sit at the same
  distance from OK, the whole quarter of the ring presses that arrow (hold to repeat), and
  Back / Home sit below its sides.
- Desktop: a strip at the top of the panel says when the keyboard is captured for the TV
  (Esc releases it); it hides while a text field is open.
- Windows (installed from source): Fling runs as `Fling.exe` with its own name and icon, so Task
  Manager lists "Fling" as one process instead of two "Python" ones.
- macOS: the same round d-pad and "keyboard captured" strip; Esc closes the panel.

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

- Authorship line "Fling by Damion Rashford & Tatiana · unofficial port",
  linking to the original repository.

### Security

- Release clean-up: the APK's manual-IP hint now shows an example address
  instead of a real one, and the TV's name is hidden in the screenshots.

[unreleased]: https://github.com/orfixe/fling-port/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/orfixe/fling-port/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/orfixe/fling-port/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/orfixe/fling-port/releases/tag/v0.1.0
