# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is an unofficial port of [Fling](https://github.com/tatianathevisionary/fling)
by Damion Rashford and Tatiana; see [CREDITS.md](CREDITS.md).

## [Unreleased]

### Added

- Linux: choose between several TVs from the panel footer, the tray menu and the
  Connect TV window; find TVs again; add a TV by IP address (named from its
  own device description); TVs with identical names are told apart by address;
  paired TVs are marked.
- Android: built-in "How to use" guide ("?" button, shown once on first
  launch), English and Russian; drafted by a local LLM, checked and completed.
- Android: the same TV chooser — paired / by-IP marks, identical names told
  apart by address, TVs added by IP are remembered and can be removed.

### Fixed

- IP validation accepts only 0–255 per number; the TV-name request closes its
  connection; removing the only TV on Android disconnects from it
  (found in a local-LLM code review, verified by hand).

### Changed

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

[unreleased]: https://github.com/wsisyeqr/fling-linux-android/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/wsisyeqr/fling-linux-android/releases/tag/v0.1.0
