<p align="right"><a href="install-windows.md">🇬🇧 English</a> &nbsp;·&nbsp; <a href="install-windows.ru.md">🇷🇺 Русский</a></p>

# Fling for Windows and the browser extension — step-by-step install

← [Back to the README](../README.md)

Fling is an icon next to the clock that sends videos from your browser to the TV
(Chromecast / Google TV) and turns the PC into a remote: power, apps, d-pad, volume,
typing, voice search. It's an unofficial port of the macOS app
[Fling](https://github.com/tatianathevisionary/fling) by Damion Rashford and Tatiana.

Two parts:

1. **The Fling app** on the PC — it talks to the TV.
2. **The Fling extension** in the browser — tells the app which tab is open and adds a
   "cast" button to the browser. Fling works without it (clipboard links, remote), but then
   "Cast this tab" only knows about Firefox.

## Requirements

| | |
|---|---|
| Windows | 10 (1809 or newer) or 11, 64-bit. ARM laptops (Snapdragon) too, via Windows 11's x64 emulation |
| TV | Chromecast, Google TV, or a TV with Android TV / Google TV built in |
| Network | PC and TV on the **same** network (same router / Wi-Fi) |
| Rights | no administrator needed — installs for your user only |

## Part 1. The app

### Option A — ready-made build (easiest)

1. Download **`Fling-Windows-…zip`** from [Releases](https://github.com/orfixe/fling-port/releases).
2. Unzip it somewhere permanent, e.g. `Documents\Fling`.
3. Double-click **`Install shortcuts.cmd`** in that folder: Start menu entry, the `fling`
   command, and Fling starts. (Double-clicking `Fling.exe` also works.)
4. **SmartScreen** may say "Windows protected your PC" — the build isn't signed with a paid
   certificate. Click **More info → Run anyway**. Some antivirus products are also wary of
   unsigned apps; if Fling.exe lands in quarantine, add the Fling folder to the exclusions.

No Python needed — everything is bundled.

### Option B — from source

1. **Code → Download ZIP** (or `git clone https://github.com/orfixe/fling-port.git`).
2. Double-click **`windows\install.cmd`**. It installs [uv](https://docs.astral.sh/uv/), a
   private Python 3.12, Qt, [catt](https://github.com/skorokithakis/catt) and Google's ADB
   (≈300 MB, 2–5 min) and starts Fling.

Everything lives in `%LOCALAPPDATA%\Fling`; settings in `%APPDATA%\Fling\fling.json`.
Update: run `install.cmd` again. Remove: `uninstall.cmd`.

### First start

1. **Windows Firewall** asks whether Fling (or Python) may use the network — tick
   **Private networks** → **Allow**. Without it Fling can't find the TV.
2. The Fling icon appears **bottom right, next to the clock** (click **^** if hidden and drag it
   onto the taskbar).
3. **Left-click** opens the panel, **right-click** the menu.
4. After ~10 s the TV's name shows at the bottom of the panel with a green dot.
5. **Pair the remote once:** "Pair TV remote…" → type the 6-character code shown on the TV.
   For the real title and artwork of whatever plays on the TV, open **Connect TV… → Find and
   connect everything** and enable ADB as the window explains.

## Part 2. The browser extension

A Fling button in the toolbar (cast, pause, seek, volume, remote), "Cast … to TV" in the
right-click menu on links and videos, and **Alt+Shift+C**. If the page's video is already
playing, the TV resumes at the same spot.

Files: the `extension` folder of the ready-made build, `%LOCALAPPDATA%\Fling\extension` after a
source install, or `fling-chrome-…zip` / `fling-firefox-…zip` on the Releases page.

### Chrome, Edge, Brave, Opera, Vivaldi, Yandex

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode**.
3. **Load unpacked** → choose the **`extension\chrome`** folder.
4. Pin Fling via the puzzle icon 🧩. Click it: a green dot and your TV's name mean it works.

Keep the folder where it is — the browser loads the extension from it.

### Firefox

Until the extension is on addons.mozilla.org, regular Firefox loads it temporarily (until restart):
`about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → `fling-firefox-…zip`.
Permanently: Firefox Developer Edition / Nightly with `xpinstall.signatures.required = false`,
then `about:addons` → ⚙ → **Install Add-on From File…**.

### Safari (Mac)

Safari is Mac-only, so its extension works with Fling running on that Mac
(`python3 desktop/fling_app.py`). Build with Xcode: `./extension/safari/build-safari.sh`, open
the resulting `Fling.app` once, allow unsigned extensions in Safari's Develop menu, and enable
Fling in Safari → Settings → Extensions.

### Extension settings

- **Tell the Fling app which tab is open** — the address goes only to Fling on this computer
  (127.0.0.1). Private windows are never shared.
- **Pause the video here after casting.**
- **Notify when the TV moves to another video** — same switch as in the panel.

## Everyday use

| What | How |
|---|---|
| Cast the tab | the extension's button, **Alt+Shift+C** in the browser, or **Win+Shift+C** anywhere (**Ctrl+Alt+Shift+C** if another app owns it — the panel shows which) |
| Cast a link | copy it → "Cast copied link", or right-click the link → "Cast link to TV" |
| Pause / seek | Space, ← / → in the panel or the extension popup |
| TV arrows | the d-pad, or scroll over the panel |
| Next-video notifications | on by default (green bell); click "Notify when the video changes" to turn off (red bell) |
| Language, compact view | **?** (this guide) and ⤡ in the panel's top-right corner; the small flag at the bottom right switches 🇬🇧 / 🇷🇺 |
| Start with Windows | "Start at login" |
| Commands | `fling`, `fling setup`, `fling cast-tab`, `fling clip`, `fling screenshot`, `fling help`, `fling quit` |

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No device found" | Same network? Allow Fling/Python on **Private** networks in Windows Firewall; set your network profile to **Private**. VPNs, guest Wi-Fi and router "client isolation" block discovery — add the TV by IP ("TV…" in the panel footer) |
| Extension says the app isn't running | Start Fling. If port 47613 is taken, set `"bridge_port"` in `fling.json` and the same port in the extension settings |
| "Cast this tab" is grey | Install the extension and open a video tab |
| Pause ignored on YouTube | Pair the remote — YouTube on the TV ignores Cast pause |
| Mic silent | Settings → Privacy & security → Microphone → allow desktop apps. On laptops with vendor audio effects (Realtek, Intel, AMD, Dolby) try turning noise suppression off in the vendor's app |
| Win+Shift+C does nothing | Another app (often vendor laptop utilities or GPU overlays) owns it; Fling falls back to Ctrl+Alt+Shift+C |
| Black panel corners (Remote Desktop, VM, old graphics driver) | Usually detected automatically; otherwise add `"solid_panel": true` to `fling.json` |
| Antivirus removed Fling.exe | Restore it and add an exclusion, or use Option B |

Log: `%LOCALAPPDATA%\Fling\logs\app.log`.

## Privacy

Everything stays on your network. Fling only talks to your TV and youtube.com (titles and
artwork). The extension sends the tab address only to Fling on the same computer.
No analytics, no accounts, no servers.
