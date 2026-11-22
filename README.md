<p align="right">
  <a href="README.md">🇬🇧 English</a> &nbsp;·&nbsp; <a href="README.ru.md">🇷🇺 Русский</a>
</p>

# Fling

Fling is the icon next to the clock. From there you send a browser tab, or a link, to a Chromecast or a Google TV, and you drive the TV: power, apps, the d-pad, volume, typing, voice search.

There is a Windows zip, a Linux installer, an Android apk, and a browser extension. The computer or the phone has to be on the same network as the TV. If it is not, Fling will not see it.

This is an unofficial port of [Fling](https://github.com/tatianathevisionary/fling), the macOS app. [Damion Rashford](https://github.com/damionrashford) wrote that code. [Tatiana](https://github.com/tatianathevisionary) published the repository. The idea, the design, the wording, the app catalog and the protocol work are theirs. This repository is not their project, and they have not endorsed it. It is a rewrite in Python/Qt, Java and JavaScript. Not a fork. No Swift was copied. The original has no license, so this stays private until they agree. More on that is in [CREDITS.md](CREDITS.md).

Tested on Windows 11 with Edge and Chrome, Kubuntu 26.04, Android 17, and a Google TV. The Firefox and Safari builds of the extension are in the repo. They have not been tried. If you try them, a note about how it went would help.

The panel, the phone app and the extension are in English and Russian.

## Windows

Windows 10 (1809 or newer) or 11, 64-bit. ARM laptops too, through the x64 emulation in Windows 11. No administrator rights.

Download `Fling-Windows-….zip` from [Releases](https://github.com/orfixe/fling-port/releases). Unzip it somewhere you will keep. Not inside the archive, and not in Downloads if you clear that folder. Double-click **Install shortcuts.cmd**. That adds a Start menu entry and the `fling` command, and starts Fling. Double-clicking `Fling.exe` also starts it, just without the shortcuts. Python, Qt, catt and yt-dlp are already in the zip.

SmartScreen will probably say Windows protected your PC. The build is unsigned. **More info → Run anyway**. Some antivirus tools quarantine an unsigned exe. If `Fling.exe` vanishes, restore it and exclude the folder.

The first launch asks the firewall. Allow Fling on **Private networks**. Skip that and it cannot find the TV.

From source, if you would rather: double-click `windows\install.cmd`. It installs [uv](https://docs.astral.sh/uv/), a private Python 3.12, PyQt6, catt and ADB into `%LOCALAPPDATA%\Fling`, then the Start menu entry and `fling`. `windows\uninstall.cmd` removes what that script installed.

Every click, including the extension, is in [docs/install-windows.md](docs/install-windows.md) ([по-русски](docs/install-windows.ru.md)).

## Browser extension

Install the app first. The extension is how the panel learns which tab you actually have open, in any browser, not only Firefox. Without it, Cast this tab reads Firefox's session file and ignores the rest. If that pickup feels slow, set `browser.sessionstore.interval` to `2000` in `about:config`.

With the extension you get a toolbar popup. Cast this tab, or resume on the TV at the spot the page is already playing. Now playing, pause, seek, volume, the remote. Right-click a link, a video or the page for the cast item. **Alt+Shift+C** in the browser. **Win+Shift+C** anywhere on Windows. If another app already owns that shortcut, Fling uses Ctrl+Alt+Shift+C, and the panel tells you which one is live.

It talks only to Fling on this computer, at `http://127.0.0.1:47613`. It will not answer a web page. Private windows are never sent.

| Browser | How |
|---|---|
| Chrome, Edge, Brave, Opera, Vivaldi, Yandex | Open the extensions page (`chrome://extensions`, Edge: `edge://extensions`, Yandex: `browser://extensions`, Opera: `opera://extensions`). Turn on Developer mode. **Load unpacked**. Pick `extension\chrome` from the Windows zip, `%LOCALAPPDATA%\Fling\extension\chrome` after a source install, or an unzipped `fling-chrome-….zip` from Releases. Leave that folder where the browser found it. |
| Firefox | Not tested. `about:debugging` → **Load Temporary Add-on** → `fling-firefox-….zip`. It lasts until Firefox quits. A permanent install needs Developer Edition until it is on AMO. |
| Safari | Not tested. Mac only, and Fling has to be running on that Mac. `extension/safari/build-safari.sh`, which needs Xcode. |

From a git checkout, `python extension/build.py` writes a loadable folder at `extension/dist/chrome`.

The click-by-click version is [Part 2 of the Windows guide](docs/install-windows.md#part-2-the-browser-extension).

## Linux

Tested on Kubuntu 26.04, KDE Plasma 6, Wayland. Another desktop with a system tray should be enough. The global shortcut and the blur are KDE.

```sh
git clone https://github.com/orfixe/fling-port.git && cd fling-port/linux
./install.sh
fling
```

`install.sh` installs the system packages (PyQt6, dbus, lz4, adb), [uv](https://docs.astral.sh/uv/), catt, a private Python venv in `~/.local/share/fling`, launchers in `~/.local/bin`, menu entries, and **Meta+Shift+C**. Log in again before that shortcut shows up. `./install.sh --uninstall` takes the install back out. It does not install the browser extension. Use the release zip, or build it as above.

## Android

Android 8.0 or newer. Take `Fling.apk` from [Releases](https://github.com/orfixe/fling-port/releases), copy it to the phone, open it, and allow installs from that source. Same Wi-Fi as the TV.

In YouTube or a browser: Share → **Cast to TV**. Volume keys control the TV while the app is open. You can add a Pause on TV tile to Quick Settings. A swipe on the screen is the d-pad.

If you already have 0.1.0, uninstall it first. This apk is signed with a different key, and Android will not install it on top. You will need to pair the TV remote again.

To build it yourself: plain Java, no Gradle, no Google Play Services. You need `ANDROID_HOME` (platform android-36 and build-tools) and a JDK 17 or newer.

```sh
cd android
./build.sh
adb install -r Fling.apk
```

## Commands

Same names in PowerShell, cmd, and a Linux shell.

```text
fling             open the panel (starts Fling if it is not running)
fling tray        tray icon only
fling setup       the Connect TV window
fling help        the built-in guide
fling screenshot  a TV screenshot; needs ADB; saved under Pictures/Fling
fling cast-tab    cast the current browser tab
fling clip        cast the link on the clipboard
fling quit        exit
fling tui         a terminal remote (Linux only)
```

In the panel, Space pauses, left and right jump 30 seconds, Ctrl+. stops, scrolling is the d-pad, Esc closes it. When the TV moves to another video or track you get a notification. The bell turns that off. A small flag at the bottom of the panel switches language.

## Connecting the TV

Open **Connect TV…** from the panel, the tray menu, or `fling setup`, then **Find and connect everything**. You can see whether a step finished, is still working, needs you at the TV, or failed.

It searches the local network. If there are several TVs, pick one. The remote step puts a 6-character code on the TV. Type it, or use the on-screen 0–9 and A–F keys. A Russian keyboard layout is translated.

For the real title, artwork and position, turn on ADB. The TV asks *Allow debugging?* — choose **Always allow**. If ADB is off: *Settings → System → About → tap "Android TV OS build" 7 times*, then *Settings → System → Developer options → USB debugging and Wireless debugging*. If the TV shows a pairing port and code, enter them in the window.

YouTube's link-with-TV-code is optional: *YouTube → Settings → Link with TV code*, 12 digits. On Linux you can also pair a Bluetooth remote, as a backup. On the TV: *Settings → Remotes & Accessories → Pair accessory → "Fling Remote"*. Confirm if KDE asks. The window stays visible for 3 minutes. Windows does not let an app act as a Bluetooth keyboard, so that path is Linux only.

Cast pause does nothing in the TV's own YouTube app until the remote is paired. That app also reports a frozen Cast session. ADB is what shows what is actually playing.

You can cast YouTube (it opens in the TV's YouTube app), direct media (mp4, mkv, webm, mp3, m3u8, mpd, and the like), and most sites [yt-dlp](https://github.com/yt-dlp/yt-dlp) understands. DRM services such as Netflix can be launched and controlled. They cannot be cast. A plain web page is not a video page.

A black screenshot is usually DRM. The video surface is protected.

Logs: `%LOCALAPPDATA%\Fling\logs\app.log` on Windows, `~/.cache/fling/app.log` and `~/.cache/fling/bt.log` on Linux. Firewall after the fact, the microphone, black panel corners, a missing tray icon: the [Windows guide](docs/install-windows.md#troubleshooting).

## Privacy

It stays on your network. Fling talks to the TV, to youtube.com when it looks up a title or artwork, and to the YouTube Lounge API if you linked a code. The extension sends a tab address only to Fling on this computer, and never from a private window. Pairing certificates live in `%LOCALAPPDATA%\Fling\atv\` on Windows, `~/.local/share/fling/atv/` on Linux, and the app's private storage on Android. No analytics. No account.

## The other documents

How the pieces fit, and why it speaks several protocols, is in [docs/architecture.md](docs/architecture.md) and [docs/protocols.md](docs/protocols.md). Building and cutting a release: [docs/development.md](docs/development.md). What changed: [CHANGELOG.md](CHANGELOG.md).

## Credits and license

[Fling](https://github.com/tatianathevisionary/fling) is [Damion Rashford](https://github.com/damionrashford) and [Tatiana](https://github.com/tatianathevisionary). This port exists because of their work. Libraries are listed in [CREDITS.md](CREDITS.md).

Code in this repository is [MIT](LICENSE). The original Fling — design, texts, idea — is theirs. The Windows zip bundles PyQt6, which is GPL-3.0. The source for that build is this repository.
