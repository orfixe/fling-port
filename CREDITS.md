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

**Licensing note.** At the time of writing the original repository has no license file,
which means all rights are reserved by its authors. This repository is therefore kept
**private** until they give permission to publish it.

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

## Contributors

- [orfixe](https://github.com/orfixe) — maintains this unofficial port, tests it on a real Google TV.
  Not the author of Fling.
- Claude (Anthropic) — implementation, with help from a local LLM (gpt-oss-20b) for icons and translations.
