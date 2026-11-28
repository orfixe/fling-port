# Credits

## Original work

**Fling — "Your TV, from the Mac menu bar."** — <https://github.com/tatianathevisionary/fling>

| Who | Role |
|---|---|
| **[Damion Rashford](https://github.com/damionrashford)** | Author of the original Fling code (all commits in the original repository) |
| **[Tatiana](https://github.com/tatianathevisionary)** | Owner and publisher of the original repository |

Fling is **their** project. This repository is an unofficial port and is not affiliated with or
endorsed by them.

This project is a re-implementation of Fling for Windows, Linux (KDE Plasma), Android
and the browser. It follows
the original closely:

- the panel layout (cast section, "On the TV" remote, device footer), wording and keyboard shortcuts;
- the TV app catalog and its app links (`TVApp.swift`);
- the handling of catt output and errors (`CattParser.swift`, `CattClient.swift`);
- the Android TV Remote v2 message layout and pairing secret (`ATVMessages.swift`,
  `ATVCertificate.swift`), the scroll-to-d-pad behaviour (`ScrollNav.swift`).

No Swift source was copied; everything was rewritten in Python/Qt, Java and JavaScript.

**Licensing note.** On 2026-11-26 the authors licensed the original repository under the
[Boost Software License 1.0](https://github.com/tatianathevisionary/fling/blob/main/LICENSE)
and invited contributions. The macOS build (`macos/`) compiles the original Swift sources under
that license; `macos/RemoteView.swift` is a modified copy of the original's file, and the
license text is in [`macos/LICENSE-fling.txt`](macos/LICENSE-fling.txt). The rest of this port
is its own code under the MIT License.

**The logo.** Fling's icon — the screen with the white dot and the trail of bubbles, on the
blue-to-violet square — is the original Fling logo (`.github/logo.svg`, by Damion Rashford,
2026-08-29), used under the same Boost Software License 1.0. This port redraws it in code
(`desktop/fling_app.py` `logo_pixmap`, `android/res/drawable/ic_fling_*`) and animates it.

## Libraries and tools

| Project | Used for | License |
|---|---|---|
| [catt](https://github.com/skorokithakis/catt) | casting from Linux | BSD-2-Clause |
| [androidtvremote2](https://github.com/tronikos/androidtvremote2) | Android TV Remote protocol (Linux) | Apache-2.0 |
| [pyytlounge](https://github.com/FabioGNR/pyytlounge) | YouTube Lounge API | MIT |
| [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) | Linux and Windows UI | GPL-3.0 / commercial |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | microphone on Windows | MIT |
| [lz4](https://github.com/python-lz4/python-lz4) | reading Firefox's session file | BSD-3-Clause |
| [PyInstaller](https://pyinstaller.org/) | the standalone Windows build | GPL-2.0 with bootloader exception |
| [uv](https://github.com/astral-sh/uv) | Python and catt install | Apache-2.0 / MIT |
| [BlueZ](http://www.bluez.org/) | Bluetooth LE HID remote | GPL-2.0 |
| [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) | ADB | Apache-2.0 |
| [scrcpy](https://github.com/Genymobile/scrcpy) (with SDL, FFmpeg, libusb) | LIVE: TV screen and mouse | Apache-2.0 (SDL: Zlib, FFmpeg / libusb: LGPL-2.1) |
| [PyChromecast](https://github.com/home-assistant-libs/pychromecast) | lasting Cast connection | MIT |
| [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) | finding TVs | LGPL-2.1 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | video links for casting | Unlicense |

The complete list with what each build bundles: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Contributors

- [orfixe](https://github.com/orfixe) — maintains this unofficial port, tests it on a real Google TV.
  Not the author of Fling.
- Claude (Anthropic) — implementation, with help from a local LLM (gpt-oss-20b) for icons and translations.
