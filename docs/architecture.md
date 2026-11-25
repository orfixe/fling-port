# Architecture

## Overview

Three programs share one design and the same TV protocols:

- **Desktop** — `desktop/fling_app.py`, one PyQt6 app for Windows and Linux.
  OS differences live in `desktop/fling_platform.py`. The Bluetooth remote
  (`linux/bt_remote.py`) is a separate process and only runs on Linux.
  Casting uses `catt`; the TV remote uses `androidtvremote2`; YouTube Lounge
  uses `pyytlounge`; now-playing uses the `adb` CLI.
- **Browser extension** — `extension/src`, one source built for Chrome, Firefox
  and Safari. It talks only to the desktop app, on `127.0.0.1`.
- **Android** — `android/src/app/fling/*.java`, plain Java, its own clients for
  every protocol, no third-party libraries.

## Desktop app (Linux and Windows)

`desktop/fling_app.py` is one PyQt6 program for both systems. It never calls an OS tool
directly: paths, subprocesses (no console windows on Windows), process checks, notifications,
autostart (KDE autostart file / `HKCU\…\Run`), the system-wide shortcut (KDE kglobalaccel from
`install.sh` / `RegisterHotKey` on a private thread), the microphone (`parecord`/`pw-record` /
`sounddevice` with resampling to 8 kHz mono) and the Pictures folder live in
`desktop/fling_platform.py`. The Bluetooth remote is Linux-only and hidden on Windows.

| | Linux | Windows |
|---|---|---|
| Settings | `~/.config/fling.json` | `%APPDATA%\Fling\fling.json` |
| Pairing, venv, adb | `~/.local/share/fling/` | `%LOCALAPPDATA%\Fling\` |
| Log | `~/.cache/fling/app.log` | `%LOCALAPPDATA%\Fling\logs\app.log` |

## Media: files, queues, slideshows

`desktop/fling_media.py` decides what can be cast and how; `fling_app.py` only calls into it.

- **Formats.** Played as they are: mp4, m4v, mov, webm, mkv; mp3, m4a, aac, flac, wav, ogg, opus;
  jpg, png, gif, bmp. Re-encoded to JPEG first: webp, tiff, heic/avif (when Qt can read them)
  and photos larger than 4K. Chromecast can't play avi, wmv, flv, mpg, ts and similar: a single
  such file is copied to the TV over ADB and opened in the TV's own player.
- **Queue.** Several files, a folder (natural order, three levels deep, up to 1000 files) or an
  m3u / m3u8 / pls playlist play one after another, with shuffle and repeat (all / one).
  Each item is one lasting `catt cast` process: catt serves a local file over HTTP only while
  the TV plays it, `-b` keeps it alive for web links. When the process ends, the media status
  pushed over the in-process Cast link says why the TV went idle: `FINISHED` moves to the next
  item, anything else (stopped on the TV, another app took over) pauses the queue.
- **Slideshow.** Pictures change every N seconds (3–60, 7 by default); play/pause pauses the timer.
- **Subtitles.** `film.srt`, `film.ru.srt`, `Subs/film.srt` next to a video are found
  automatically (the interface language wins) or chosen in the ⋯ menu. They are converted to
  UTF-8 WebVTT first: catt reads SRT only as UTF-8 or Latin-1 (Windows-1251 subtitles turned
  into mojibake) and can't read ASS/SSA.
- **Start position.** A browser's position, then `?t=` / `#t=` in the link (YouTube, Twitch,
  Vimeo, media fragments), then the remembered position: for anything 10 minutes or longer
  Fling remembers where it was stopped (`positions` in `fling.json`, 150 entries) and forgets
  it once watched to the end.
- **Recent.** The last 25 casts (links, files, folders, playlists, slideshows) are in
  *Open on TV…* in the panel and the tray menu.
- **Speed.** 0.5×–2× for media in the Default Media Receiver (`SET_PLAYBACK_RATE`).
- **Safety.** Only `http(s)` links come from outside. Network paths (`\\server\share`,
  `//server/share`, `file://host/…`) are refused before any file call, also inside playlists
  and dropped folders, because touching them sends the Windows login hash to that server.

## Browser bridge

`desktop/fling_bridge.py` serves `http://127.0.0.1:47613` (`"bridge_port"` in `fling.json`,
`"bridge": false` turns it off) on a background thread; every request is executed on the Qt
thread and answered with a JSON snapshot of the state.

- `GET /v1/status` — device, devices, now playing, volume, remote state, last error.
- `POST /v1/tab` — the extension reports the active tab `{url, title, browser}` on tab
  switches, page loads, window focus and once a minute; a report older than 3 minutes counts
  as "browser closed" and Fling falls back to Firefox's session store.
- `POST /v1/action` — `cast {url, title, start, media_src}`, `play_pause`, `seek {seconds}`,
  `stop`, `volume {level}`, `key {key}`, `launch {app}`, `text {text}`, `wake`,
  `select_device {ip}`, `rescan`, `open_panel`, `open_setup`, `pair_remote`, `notify_track {on}`.

Only browser extensions and local tools get in: the Host header must be the loopback address
(DNS rebinding), an Origin, if present, must be `chrome-extension://`, `moz-extension://` or
`safari-web-extension://`, and POSTs need the custom `X-Fling: 1` header, which forces a CORS
preflight that web origins are refused.

The extension (`extension/src`) is Manifest V3: a service worker in Chrome/Edge, an event page
in Firefox and Safari. When it casts, it injects a small probe into the page (`activeTab`) to read
the main `<video>`/`<audio>` position and its `http(s)` source, so the TV can resume at the same
spot and play streams yt-dlp doesn't know.

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
