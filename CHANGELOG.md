# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is an unofficial port of [Fling](https://github.com/tatianathevisionary/fling)
by Damion Rashford and Tatiana; see [CREDITS.md](CREDITS.md).

## [Unreleased]

## [0.2.0] - 2026-11-21

### Added

- **Windows port** (Windows 10 1809+ / 11, x64 and ARM64 via emulation): the same tray panel,
  Connect TV window, remote, voice search and guide as on Linux. Standalone `Fling.exe`
  (PyInstaller, catt and yt-dlp bundled) or install from source with `windows\install.cmd`
  (no admin rights); Start menu entry, `fling` command, Start at login, Windows toasts.
- Windows: system-wide **Win+Shift+C**, falling back to Ctrl+Alt+Shift+C when another app
  owns it; the panel shows the shortcut that actually works.
- **Browser extension** for Chrome, Edge, Brave, Opera, Vivaldi, Yandex, Firefox and Safari
  (one source, `extension/build.py`): popup remote, *Cast this tab* with **resume at the
  page's position**, the page's own stream for sites yt-dlp can't open, right-click
  *Cast link / video / page*, Alt+Shift+C, pause in the browser after casting, EN/RU.
- Desktop ↔ extension bridge on `127.0.0.1:47613` (`desktop/fling_bridge.py`): accepts
  extension origins and local tools only (Host and Origin checks, `X-Fling` header).
  The panel's *Cast this tab* now works with any browser that has the extension.
- Notification when the TV moves to another video or track, with a *Notify about the next
  video* switch in the panel, the tray menu and the extension settings.
- A small flag at the bottom of the panel switches 🇬🇧 / 🇷🇺. Panel corner: **?** opens the guide, and a **compact view** button turns playback into one row of icon keys (labels in tooltips) and hides the
  remote and the housekeeping rows. The notification switch shows a green / red bell;
  new icons for start at login and quit; clearer labels (*Pair TV remote…*, *Cast copied link*,
  *Select* next to the TV's name).
- Android: `Fling.apk` 0.2.0 (version code 2) with the "How to use" guide and the TV chooser.
  It is signed with a new debug key, so a phone with 0.1.0 must uninstall the old app first
  (and pair the TV remote again).
- `android/build.sh` also builds on Windows (Git Bash) and creates the debug key when missing.
- Step-by-step Windows guide in English and Russian (`docs/install-windows*.md`),
  Russian README, language flags.
- Android: first-run tips one at a time over the real screen (spotlight + small card, Material
  motion) instead of a long dialog; the full guide stays one tap away.

- Linux: choose between several TVs from the panel footer, the tray menu and the
  Connect TV window; find TVs again; add a TV by IP address (named from its
  own device description); TVs with identical names are told apart by address;
  paired TVs are marked.
- Linux: built-in "How to use" guide (13 sections, English and Russian):
  panel row, tray menu, `fling help`, shown once on first start.
- Android: built-in "How to use" guide ("?" button, shown once on first
  launch), English and Russian; drafted by a local LLM, checked and completed.
- Android: the same TV chooser — paired / by-IP marks, identical names told
  apart by address, TVs added by IP are remembered and can be removed.

### Fixed

- IP validation accepts only 0–255 per number; the TV-name request closes its
  connection; removing the only TV on Android disconnects from it
  (found in a local-LLM code review, verified by hand).

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

[unreleased]: https://github.com/orfixe/fling-port/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/orfixe/fling-port/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/orfixe/fling-port/releases/tag/v0.1.0
