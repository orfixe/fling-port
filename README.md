<p align="right">
  <a href="README.md">🇬🇧 English</a> &nbsp;·&nbsp; <a href="README.ru.md">🇷🇺 Русский</a>
</p>

<div align="center">

<img src="docs/screenshots/logo.png" width="96" alt="Fling logo">

# Fling for Windows, Linux & Android

**Your TV, from the Windows tray, the KDE panel, your browser and your phone.**

Cast a browser tab or a link to a Chromecast / Google TV — and drive the TV itself:
power, apps, d-pad, volume, typing, voice search.

[![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)](docs/install-windows.md)
[![Linux KDE](https://img.shields.io/badge/Linux-KDE%20Plasma-1D99F3?logo=kde&logoColor=white)](#install--linux)
[![Android 8+](https://img.shields.io/badge/Android-8%2B-3DDC84?logo=android&logoColor=white)](#install--android)
<br>
[![Chrome / Edge](https://img.shields.io/badge/Chrome%20%2F%20Edge-extension-4285F4?logo=googlechrome&logoColor=white)](#browser-extension)
[![Firefox](https://img.shields.io/badge/Firefox-extension-FF7139?logo=firefoxbrowser&logoColor=white)](#browser-extension)
[![Safari](https://img.shields.io/badge/Safari-extension-006CFF?logo=safari&logoColor=white)](#browser-extension)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

<img src="docs/screenshots/hero.png" width="100%" alt="Fling on Windows, in the browser, on KDE Plasma and on Android">

<sub>Windows panel · browser extension · KDE Plasma · Android</sub>

</div>

> [!WARNING]
> **Fling is in development.** Things change from release to release and some parts are still
> rough (LIVE, the ADB console, scenes, queues and the Tools window are new). Please report what breaks in
> [Issues](https://github.com/orfixe/fling-port/issues).

> **Authorship.** An **unofficial** port of **[Fling](https://github.com/tatianathevisionary/fling)** —
> the macOS menu-bar caster and TV remote created by **[Damion Rashford](https://github.com/damionrashford)**
> (author of the original code) and published by **[Tatiana](https://github.com/tatianathevisionary)**.
> Its idea, design, wording, app catalog and protocol work belong to them. This repository is not
> their project and is not affiliated with or endorsed by them; it only brings their app to
> Windows, Linux, Android and the browser.
>
> **Status:** private preview. An independent re-implementation (Python/Qt, Java, JavaScript),
> not a fork: no Swift code was copied. The original repository has no license yet — this project
> stays private until its author agrees. See [CREDITS.md](CREDITS.md).

---

## Quick start

| I have… | Do this |
|---|---|
| **Windows 10 / 11** | Download `Fling-Setup-….exe` from [Releases](../../releases), [verify your download](SECURITY.md#is-my-copy-the-real-fling) if you like, and run it. Step by step: **[Windows guide](docs/install-windows.md)** |
| **Chrome / Edge / Firefox / Safari** | Install the app first, then the [browser extension](#browser-extension) |
| **Linux (KDE)** | `git clone` and `linux/install.sh` — [details](#install--linux) |
| **Android phone** | Install `Fling.apk` from [Releases](../../releases) — [details](#install--android) |

The computer / phone and the TV must be on the same network.

**Tested on:** Windows 11 with Edge and Chrome, Kubuntu 26.04 (KDE Plasma 6), Android 17,
a Google TV set. The Firefox and Safari builds of the extension haven't been tried yet —
reports are welcome.

**Contents** · [Features](#features) · [How it works](#how-it-works) ·
[Windows](#install--windows) · [Browser extension](#browser-extension) · [Linux](#install--linux) ·
[Android](#install--android) · [Connecting the TV](#connecting-the-tv) · [Usage](#usage) ·
[Troubleshooting](#troubleshooting) · [Privacy](#privacy) · [Project layout](#project-layout) ·
[Documentation](#documentation) · [Credits](#credits) · [License](#license)

## Features

| | Windows | Linux (KDE) | Android | Browser extension |
|---|:---:|:---:|:---:|:---:|
| Tray/panel UI modelled on the macOS Fling popover | ✓ | ✓ | ✓ | ✓ popup |
| Cast the current browser tab | ✓ **Win+Shift+C** | ✓ **Meta+Shift+C** | — | ✓ **Alt+Shift+C** |
| Resume on the TV where the page's video is | ✓ ¹ | ✓ ¹ | — | ✓ |
| Cast a link: clipboard / share sheet / right-click menu | ✓ | ✓ | ✓ | ✓ |
| Play / pause, ±30 s, volume, stop | ✓ | ✓ | ✓ | ✓ |
| **Real now-playing** (title, artist, artwork, time) via ADB | ✓ | ✓ | ✓ | ✓ |
| Notification when the TV moves to the next video / track (can be turned off) | ✓ | ✓ | — | — |
| YouTube "Link with TV code" (live video + position) | ✓ | ✓ | — | — |
| TV remote: power, app launcher, d-pad, volume, typing | ✓ | ✓ | ✓ | ✓ |
| Voice search through the microphone | ✓ | ✓ | ✓ | — |
| Laptop as a **Bluetooth LE remote** (HID over GATT) | — ² | ✓ | — | — |
| One-click **Connect TV** window | ✓ | ✓ | — | — |
| Scroll / swipe anywhere = d-pad | ✓ | ✓ | ✓ | ✓ |
| Phone volume keys, Quick Settings tile | — | — | ✓ | — |
| Remote modes (touchpad, numbers, media keys), favourite apps, scenes | ✓ | ✓ | — | — |
| Sleep timer for the TV | ✓ | ✓ | ✓ | — |
| Queue, playlists (m3u / pls), photo slideshow, subtitles, resume | ✓ | ✓ | queue ³ | — |
| Cast photos, videos and music from the phone (Share → Cast to TV) | — | — | ✓ | — |
| Playback notification with lock-screen controls | — | — | ✓ | — |
| Settings with themes and a live preview | ✓ 11 themes | ✓ 11 themes | ✓ incl. Material You | ✓ options |
| **Tools** window: find, install and update adb / scrcpy | ✓ | ✓ | — | — |
| Signed releases, guard against foreign processes | ✓ | — | — | — |
| English / Russian UI | ✓ | ✓ | ✓ | ✓ |
| TV screenshot over ADB (saved to Pictures/Fling) | ✓ | ✓ | ✓ | — |
| Install an APK on the TV over ADB (pick a file, or right-click it in Explorer) | ✓ | ✓ | — | — |

<sub>¹ when the extension casts the tab. ² Windows doesn't let apps act as a Bluetooth keyboard / remote.
³ several shared files play one after another.</sub>

## How it works

Fling talks to the TV over several open protocols and uses whichever is available:

| Channel | Used for | Library / implementation |
|---|---|---|
| **Google Cast v2** (TLS :8009) | casting media, play/pause/seek/volume | [catt](https://github.com/skorokithakis/catt) on Windows/Linux; own Java client on Android |
| **Android TV Remote v2** (TLS :6466/:6467) | power, keys, app links, typing, voice | [androidtvremote2](https://github.com/tronikos/androidtvremote2) on Windows/Linux; own Java client on Android |
| **ADB over network** (:5555) | what is *really* playing (`dumpsys media_session`) | platform-tools `adb` on Windows/Linux; own Java ADB client on Android |
| **YouTube Lounge API** | exact video and position in the YouTube TV app | [pyytlounge](https://github.com/FabioGNR/pyytlounge) |
| **Bluetooth LE HID** | keys when the network remote isn't paired (Linux) | BlueZ D-Bus (`linux/bt_remote.py`) |
| **Local bridge** (HTTP, 127.0.0.1:47613) | browser extension ↔ desktop app | `desktop/fling_bridge.py`, `extension/` |

Why so many? Native YouTube on Google TV reports a *frozen* Cast session (stuck in
`BUFFERING`, old artwork) and ignores Cast pause. Fling detects that, hides the stale data,
and routes control through the TV remote protocol, while ADB or YouTube Lounge provide the
real title, artwork and position.

## Install — Windows

**→ Full step-by-step guide: [docs/install-windows.md](docs/install-windows.md)
([по-русски](docs/install-windows.ru.md)).**

Windows 10 (1809+) or 11, 64-bit (ARM laptops via emulation). No administrator rights needed.

- **Setup:** download **`Fling-Setup-….exe`** from [Releases](../../releases) and run it. It installs
  for your user only (no admin rights) with a Start menu entry, the `fling` command and, if you
  like, autostart; uninstall from *Settings → Apps*. Python, Qt, catt, yt-dlp and scrcpy are bundled. SmartScreen may warn about an unsigned app:
  *More info → Run anyway*.
- **Verify your download:** each release has `SHA256SUMS.txt` signed with the Fling release key —
  how to check it on any OS: [SECURITY.md](SECURITY.md#is-my-copy-the-real-fling). The installed
  app is watched by `fling-guard`, which checks Fling's files against that signed list.
- **From source:** double-click **`windows\install.cmd`** — installs [uv](https://docs.astral.sh/uv/),
  a private Python 3.12, PyQt6, catt and ADB into `%LOCALAPPDATA%\Fling`, adds Fling to the Start
  menu and the `fling` command. `windows\uninstall.cmd` removes everything.

On first start allow Fling on **Private networks** in the Windows Firewall prompt — otherwise it
can't find the TV.

## Browser extension

One extension for **Chrome, Edge, Brave, Opera, Vivaldi, Yandex, Firefox and Safari**
(`extension/src`, built with `python extension/build.py`). It is the browser half of the desktop app:

- a toolbar popup with *Cast this tab* (or **Resume on TV at 12:34** when the page's video is
  mid-play), now playing, play/pause/seek/stop, volume and the full TV remote;
- *Cast link / video / page to TV* and *Cast from this moment* in the right-click menu; timestamps
  in YouTube, Twitch and Vimeo links are kept;
- **Alt+Shift+C** in the browser, plus shortcuts you can assign for play / pause, ±30 s and volume;
- recent casts in the popup;
- tells the app which tab is active, so the panel's *Cast this tab* and the system-wide
  shortcut work with any browser, not just Firefox.

It talks **only** to the Fling app on the same computer (`http://127.0.0.1:47613`), which accepts
browser extensions and local tools and refuses web pages.

| Browser | Install |
|---|---|
| Chrome, Edge, Brave, Opera, Vivaldi, Yandex | `chrome://extensions` → Developer mode → **Load unpacked** → the `extension\chrome` folder of the Windows zip, or `fling-chrome-….zip` from Releases, unzipped |
| Firefox | `about:debugging` → **Load Temporary Add-on** → `fling-firefox-….zip` (a permanent install needs Developer Edition until it's on AMO) |
| Safari (Mac) | `extension/safari/build-safari.sh` on a Mac with Xcode; needs Fling running on that Mac (not tested yet) |

Details: [Windows guide → Part 2](docs/install-windows.md#part-2-the-browser-extension).

## Install — Linux

Tested on Kubuntu 26.04 (KDE Plasma 6, Wayland). Other desktops with a system tray should work;
the global shortcut and blur are KDE-specific.

```sh
git clone https://github.com/orfixe/fling-port.git && cd fling-port/linux
./install.sh
fling            # opens the panel; the icon lives in the system tray
```

`install.sh` installs system packages (PyQt6, dbus, lz4, adb), [uv](https://docs.astral.sh/uv/),
catt, a private Python venv in `~/.local/share/fling`, launchers in `~/.local/bin`, menu entries,
and the **Meta+Shift+C** shortcut (active after re-login). Remove everything with
`./install.sh --uninstall`.

Commands (the same on Windows, in PowerShell / cmd):

```text
fling             open the panel (starts Fling if needed)
fling tray        start only the tray icon
fling setup       open the Connect TV window
fling settings    open Settings (themes, remote, casting)
fling about       open Settings → About
fling tools       find, install or update adb and scrcpy
fling help        open the "How to use" guide
fling screenshot  save a TV screenshot (needs ADB)
fling install-apk app.apk   install an APK on the TV (needs ADB; no file = choose one)
fling cast-tab    cast the current browser tab
fling clip        cast the link from the clipboard
fling send file…  to the TV: APKs install, media play, the rest goes to Download
fling play [file|folder|playlist|link…]   play on the TV as a queue (no argument = choose files)
fling slideshow [folder] [seconds]       photo slideshow (default 7 s per photo)
fling next / fling prev                  next / previous item of the queue
fling scene "Name"                       run a scene (e.g. from Task Scheduler / cron)
fling sleep-timer N                      turn the TV off in N minutes
fling tv-on / fling tv-off               turn the TV on / off
fling quit        exit
fling tui         terminal remote (Linux)
```

## Install — Android

Requires Android 8.0+. No Google Play Services, no Gradle: the app is plain Java built with the
SDK command-line tools.

```sh
cd android
./build.sh        # needs ANDROID_HOME (platform android-36, build-tools) and a JDK 17+
adb install -r Fling.apk
```

Or copy `Fling.apk` to the phone and open it (allow installing from that source).
Phone and TV must be on the same Wi-Fi.

## Connecting the TV

Open **Connect TV…** (panel, tray menu, or `fling setup`) and press **Find and connect everything**.
Each step shows ✓ done · ⏳ working · ! needs you on the TV · ✗ failed.

1. **Find TV on local network** — mDNS/Cast discovery; pick a device if you have several.
2. **TV remote (network)** — the TV shows a 6-character code; type it or click the on-screen
   0–9 / A–F keys (a Russian keyboard layout is translated automatically).
3. **ADB** — the TV asks *"Allow debugging?"*: choose **Always allow**. If ADB is off:
   *Settings → System → About → tap "Android TV OS build" 7×*, then
   *Settings → System → Developer options → USB debugging + Wireless debugging*.
   If the TV shows a pairing port and code, enter them in the window (`adb pair`).
4. **YouTube on the TV** *(optional)* — *YouTube → Settings → Link with TV code*, enter the 12 digits.
5. **Bluetooth remote** *(backup, Linux)* — on the TV: *Settings → Remotes & Accessories → Pair accessory →
   "Fling Remote"*. KDE asks you to confirm pairing.

## Usage

- **Panel:** click the tray icon. Space = play/pause, ←/→ = ±30 s, Ctrl+. = stop,
  scroll anywhere = d-pad, Esc = close. The ⟳ button on the artwork refreshes.
- **Cast this tab:** with the [browser extension](#browser-extension) Fling knows the active tab of
  Chrome, Edge, Firefox or Safari instantly. Without it, it reads the active Firefox tab from the
  session store (set `browser.sessionstore.interval` to `2000` in `about:config` for quick pick-up).
- **Notifications:** when the TV moves to another video or track you get a notification with the
  new title; turn it off with *Notify when the video changes* in the panel or the tray menu.
- **Remote:** below the app grid switch between Arrows, Touchpad, Numbers and Media. The **…** menu
  has scenes, the sleep timer and Turn on / Turn off; pin your favourite apps per TV.
- **Files, playlists, slideshows:** drop files or folders on the panel, use *Open on TV…*, or
  `fling play` / `fling slideshow`. Subtitles next to a video are picked up automatically.
- **Settings:** the gear in the panel's corner — themes, accent colour, remote and casting options.
- **Android:** share a link to **Fling** from YouTube or a browser — it goes straight to the TV;
  share photos, videos or music (*Cast to TV*) and the phone plays them on the TV. Volume keys
  control the TV while the app is open or from the playback notification. Add the *Pause on TV*
  Quick Settings tile.

What can be cast: YouTube (opened in the TV's YouTube app), direct media files
(mp4, mkv, mov, webm, mp3, opus, m3u8, mpd, …), photos (heic, webp and tiff are converted),
local files and playlists, and most sites [yt-dlp](https://github.com/yt-dlp/yt-dlp)
understands. DRM services (Netflix etc.) can be launched and controlled but not cast.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Pause does nothing on YouTube | Pair the TV remote (Connect TV → step 2); Cast pause is ignored by native YouTube. |
| Old artwork / wrong title | That's the TV's frozen Cast session; connect ADB (step 3) for the real one. |
| "Not a video page" | Plain web pages can't be cast; open the video page itself. |
| Can't type the TV code | Use the on-screen keys under the field. |
| TV screenshot is black | DRM: video surfaces are protected; Fling uses media metadata instead. |
| Bluetooth remote not visible (Linux) | Press *Connect as Bluetooth remote* again (the window lasts 3 min); check `bluetoothctl show` lists *Human Interface Device*. |
| Panel is tiny / blurry (Linux) | Fling reads the XWayland scale from `~/.config/kwinrc`; set `QT_SCALE_FACTOR` to override. |
| Windows: TV not found, extension offline, mic, shortcuts, black corners | See the [Windows guide](docs/install-windows.md#troubleshooting). |

Logs: `~/.cache/fling/app.log`, `~/.cache/fling/bt.log` (Linux), `%LOCALAPPDATA%\Fling\logs\app.log` (Windows).

## Privacy

Everything stays on your network. Fling contacts only your TV, plus `youtube.com` for
titles/artwork lookups (oEmbed and search) and the YouTube Lounge API when you link it.
Only when you ask, it checks GitHub for a newer release (*Settings → About*) and downloads
adb / scrcpy from Google and GitHub (*Tools*).
The browser extension sends tab addresses only to the Fling app on the same computer and never
from private windows. Pairing certificates live in `~/.local/share/fling/atv/` (Linux),
`%LOCALAPPDATA%\Fling\atv\` (Windows) or the app's private storage (Android).
No analytics, no accounts.

## Project layout

```text
desktop/             shared by Windows and Linux (PyQt6)
  fling_app.py       tray + panel + Connect TV window + help window
  fling_platform.py  everything OS-specific: paths, processes, autostart, hotkey, microphone
  fling_bridge.py    local HTTP bridge for the browser extensions (127.0.0.1 only)
  fling_help.py      texts of the built-in "How to use" guide (EN + RU, Windows variants)
  sf_icons.py        line icons in SF-Symbols style
  fling_remote.py    remote modes (touchpad, numbers, media), favourite apps, scenes, sleep timer
  fling_media.py     what can be cast and how: formats, queues, playlists, slideshows, subtitles
  fling_settings.py  Settings window (Appearance, General, Remote, Casting, About)
  fling_themes.py    theme presets and the "As the system" resolution
  fling_devices.py   TV chooser window (search, cards, add by IP)
  fling_tools.py     Tools window: find / install / update adb and scrcpy
guard/               fling-guard and fling-sign (Rust): signed file list, process allowlist, CPU watchdog
security/            the release public key (fling-release.pub)
windows/
  install.ps1        install from source / update / uninstall (install.cmd, uninstall.cmd)
  build.ps1          standalone Fling.exe + release zip (PyInstaller)
  setup-shortcuts.ps1  Start menu + `fling` command for the standalone build
linux/
  bt_remote.py       Bluetooth LE HID remote service (BlueZ D-Bus)
  fling              launcher (single instance, commands)
  fling-tui          terminal remote (bash + catt)
  install.sh         installer / uninstaller
extension/
  src/               background, popup, options, locales — one source for every browser
  build.py           → dist/chrome, dist/firefox, dist/safari + zips
  safari/            Xcode wrapper script for Safari
android/
  src/app/fling/     MainActivity, Ui, Atv (TV remote), Cast (Cast v2), Adb (ADB client), Discovery (mDNS),
                     Proto (protobuf), Tls (self-signed client cert), L (i18n), PauseTile,
                     MediaServer (phone files), CastService (notification), SleepTimer, SettingsActivity
  res/, AndroidManifest.xml, build.sh
docs/                guides, architecture, protocols, screenshots
```

## Documentation

| Document | What it covers |
|---|---|
| [Windows install guide](docs/install-windows.md) · [RU](docs/install-windows.ru.md) | Step by step: app, browser extension, first start, troubleshooting |
| [Architecture](docs/architecture.md) | Components, threading, the browser bridge, now-playing sources, files |
| [Protocols](docs/protocols.md) | Cast v2, Android TV Remote v2, ADB, YouTube Lounge, Bluetooth LE HID, the bridge API |
| [Development](docs/development.md) | Building, checks, testing on a real TV, translations, releases |
| [Changelog](CHANGELOG.md) | What changed in each version |

## Credits

- **[Fling](https://github.com/tatianathevisionary/fling)** — the original macOS app, its idea,
  design, wording and protocol research. Created by **[Damion Rashford](https://github.com/damionrashford)**
  (author of the original code) and published by **[Tatiana](https://github.com/tatianathevisionary)**
  (repository owner). This port exists only because of their work.
- [catt](https://github.com/skorokithakis/catt) (BSD-2-Clause),
  [androidtvremote2](https://github.com/tronikos/androidtvremote2) (Apache-2.0),
  [pyytlounge](https://github.com/FabioGNR/pyytlounge) (MIT),
  [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) (GPL-3.0),
  [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) (MIT),
  [PyInstaller](https://pyinstaller.org/), BlueZ, Android platform-tools.

Details in [CREDITS.md](CREDITS.md).

## License

The code of this port is [MIT](LICENSE), © 2026 orfixe and the Fling port contributors. The
original Fling — its design, texts and idea — belongs to Damion Rashford and Tatiana; see
[NOTICE](NOTICE) and [CREDITS.md](CREDITS.md). The Windows build bundles third-party software
under its own licenses — PyQt6 (GPL-3.0; the complete source is this repository), scrcpy
(Apache-2.0) and others: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Redistributing the
port means keeping these files and the in-app credit line with it.
