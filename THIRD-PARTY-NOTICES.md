# Third-party notices

The Fling port uses, and its builds bundle, the software below. Each part keeps its own
license; the full license texts come with each package (in the Windows setup: inside the
`_internal` folder next to each package's metadata, and `scrcpy\LICENSE.txt`).

## Bundled in the Windows setup (Fling.exe)

| Software | Used for | License |
|---|---|---|
| [Python](https://www.python.org/) | the runtime | PSF-2.0 |
| [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) / [Qt 6](https://www.qt.io/) | the UI | GPL-3.0 (PyQt6), LGPL-3.0 (Qt) |
| [catt](https://github.com/skorokithakis/catt) | casting | BSD-2-Clause |
| [PyChromecast](https://github.com/home-assistant-libs/pychromecast) | the lasting Cast connection | MIT |
| [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) | finding TVs | LGPL-2.1-or-later |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | video links for casting | Unlicense |
| [androidtvremote2](https://github.com/tronikos/androidtvremote2) | the Android TV Remote protocol | Apache-2.0 |
| [pyytlounge](https://github.com/FabioGNR/pyytlounge) | YouTube on the TV | MIT |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) / [PortAudio](http://www.portaudio.com/) | the microphone | MIT |
| [python-lz4](https://github.com/python-lz4/python-lz4) | reading Firefox's session file | BSD-3-Clause |
| [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) (adb) | ADB | [Android SDK License](https://developer.android.com/studio/terms) (the binaries; adb's source is Apache-2.0) |
| [scrcpy](https://github.com/Genymobile/scrcpy) | LIVE: the TV screen and the mouse | Apache-2.0 |
| [SDL 3](https://www.libsdl.org/) (inside scrcpy) | scrcpy's window | Zlib |
| [FFmpeg](https://ffmpeg.org/) (inside scrcpy) | video decoding | LGPL-2.1-or-later |
| [libusb](https://libusb.info/) (inside scrcpy) | USB devices | LGPL-2.1-or-later |
| [PyInstaller](https://pyinstaller.org/) bootloader | Fling.exe | GPL-2.0 with the bootloader exception |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | the installer | Inno Setup License |

The Windows build bundles PyQt6 under GPL-3.0; the complete corresponding source of
this port is this repository. scrcpy, SDL, FFmpeg and libusb are shipped unmodified, as
released by the scrcpy project (scrcpy-win64 release zip, checked against its SHA256 sums).

## Downloaded on request (Tools window)

When a tool is missing, the Tools window (`desktop/fling_tools.py`) can download it for
the current user into Fling's data folder (`%LOCALAPPDATA%\Fling`, `~/.local/share/fling`).
Fling does not change these programs and does not redistribute them itself: each archive
comes straight from its publisher over HTTPS and is checked before anything in it runs.

| Software | From | Checked against | License |
|---|---|---|---|
| [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) (adb) | dl.google.com (Google's SDK repository) | the SHA-1 Google lists in `repository2-3.xml` | [Android SDK License](https://developer.android.com/studio/terms) |
| [scrcpy](https://github.com/Genymobile/scrcpy) (with SDL, FFmpeg, libusb and its own adb inside) | the GitHub release | the release's `SHA256SUMS.txt` (and GitHub's asset digest) | Apache-2.0 (scrcpy); see above for the parts inside |

Copies found already installed (PATH, Android SDK, WinGet, Scoop, Chocolatey, Homebrew,
system packages) are only run, or copied into Fling's folder when the user asks; their own
licenses apply.

## Downloaded on first use (Android app, LIVE)

| Software | From | Checked against | License |
|---|---|---|---|
| [scrcpy-server](https://github.com/Genymobile/scrcpy) v5.0.1 (`scrcpy-server-v5.0.1`) | the [GitHub release](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1) | SHA-256 pinned in the app (`ScrcpyProto.SERVER_SHA256`, from the release's `SHA256SUMS.txt`), checked before every use | Apache-2.0, © Genymobile and Romain Vimont |

The Android app keeps the file in its private storage and uploads it, unmodified, to the TV
(`/data/local/tmp/`), where it runs the screen capture for LIVE. A file that doesn't match
the pinned hash is deleted and never run. The scrcpy client in the app (`Scrcpy.java`,
`ScrcpyProto.java`) is Fling's own code speaking the scrcpy protocol; the APK contains no
scrcpy code. Downloaded rather than bundled: the server (~720 KB) is several times the
size of the whole APK, and the repository stays free of binaries — the pinned hash makes
the check exactly as strict as bundling would.

## Used by other parts

| Software | Used for | License |
|---|---|---|
| [uv](https://github.com/astral-sh/uv) | Python and catt install (install.ps1, Linux) | Apache-2.0 / MIT |
| [BlueZ](http://www.bluez.org/) | Bluetooth LE remote (Linux) | GPL-2.0 |
| Android SDK build tools | building the Android app | Apache-2.0 |
