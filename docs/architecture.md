# Architecture

## Overview

Two independent apps share one design and the same TV protocols:

- **Linux** — `linux/fling_app.py` (PyQt6, single file) plus `linux/bt_remote.py`
  (Bluetooth service, separate process). Uses `catt`, `androidtvremote2`,
  `pyytlounge` and the `adb` CLI.
- **Android** — `android/src/app/fling/*.java`, plain Java with its own
  implementations of every protocol and no third-party libraries.

## Linux app

| Component | Role |
|---|---|
| `State` | All app state (`QObject`). Blocking I/O runs on a `ThreadPoolExecutor` with 4 workers; results come back to the Qt thread through a `Bridge` signal. |
| `Panel` | Frameless `Qt.Popup` window, 340 px wide, placed above the tray. Wheel / touchpad scrolling over it drives the TV d-pad. |
| `Tray` | `QSystemTrayIcon` and its context menu. A 2-second timer refreshes while the panel is open or something is casting. |
| `SetupWindow` | The **Connect TV** window: find, remote pairing, ADB, YouTube, Bluetooth. |
| `ATV` | Android TV Remote session (`androidtvremote2`) on an asyncio loop in a background thread. |
| `Lounge` | YouTube Lounge client (`pyytlounge`) on the same asyncio loop. |

The app runs under XWayland (`QT_QPA_PLATFORM=xcb`) so the popup can be placed
next to the tray; the scale factor comes from `[Xwayland] Scale` in `~/.config/kwinrc`,
and KWin blur is requested with the `_KDE_NET_WM_BLUR_BEHIND_REGION` property.

A `QLocalServer` named `fling-panel` keeps one instance; `fling <command>` forwards
`show`, `toggle`, `cast-tab`, `clip`, `setup`, `screenshot` and `quit` to it.

The current Firefox tab is read from `sessionstore-backups/recovery.jsonlz4`
(a `mozLz40` header followed by an LZ4 block).

## Bluetooth remote service

`bt_remote.py` (system `python3`, `dbus-python`, GLib) registers a BlueZ GATT
application with a HID service `0x1812` — report 1 is a keyboard, report 2 a
consumer control — and a Battery service `0x180F`. It advertises as
**Fling Remote** (appearance `0x0180`) and is discoverable only during a
180-second pairing window; the desktop's own Bluetooth agent confirms pairing.
Key presses are sent to the TV as notifications on the HID report characteristics.

It is controlled over `$XDG_RUNTIME_DIR/fling-bt.sock`:
`key <name>`, `text <ascii>`, `pair [seconds]`, `status`.

## Android app

| Class | Role |
|---|---|
| `MainActivity` | UI and state. Cast status every 1.5 s; ADB now-playing every 3 s, backing off to 30 s while ADB is off or waiting for approval. |
| `Ui` | Custom views and Path-drawn icons. |
| `Atv` | Android TV Remote v2: pairing and session. |
| `Tls` | Self-signed RSA-2048 client certificate (minimal DER encoder); TLS sockets that accept the TV's self-signed certificate. |
| `Cast` | Cast v2 client. |
| `Discovery` | `NsdManager` discovery of `_googlecast._tcp`, holding a `MulticastLock`. |
| `Adb` | ADB-over-TCP client (now-playing, screenshots). |
| `Proto` | Minimal protobuf encoder / decoder. |
| `L` | English / Russian strings. |
| `PauseTile` | Quick Settings tile. |

## Now playing

Sources, highest priority first:

1. **YouTube Lounge** (Linux, if linked) — exact video and position in the YouTube app.
2. **ADB media session** — title, artist, state and position of whatever app is playing.
3. **Cast status** — what a Cast sender reported.

Native YouTube on Google TV often leaves a *frozen* Cast session behind
(`BUFFERING`, position 0, old artwork) and ignores Cast pause. Fling treats that
session as stale: its artwork and title are hidden, play / pause is sent as remote
key 85 and seeking as keys 89 / 90 (about 10 s per press).

When a source has a title but no artwork, the first YouTube search result for that
title supplies the thumbnail and duration (cached per title).

## Files

| Path | Contents |
|---|---|
| `~/.config/fling.json` | Found devices, chosen device, YouTube link, language |
| `~/.local/share/fling/atv/<ip>/` | Remote-pairing client certificate and a "paired" flag |
| `~/.local/share/fling/venv/` | Python environment created by `install.sh` |
| `~/.cache/fling/app.log`, `bt.log` | Logs |
| `~/Pictures/Fling/` | TV screenshots |
| Android app storage | Client certificates, ADB key, preferences |
