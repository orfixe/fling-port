# Development

## Layout

```text
desktop/    the app Windows and Linux both run (fling_app.py, fling_platform.py, fling_bridge.py, fling_help.py, sf_icons.py)
windows/    install from source, and the script that builds Fling.exe
linux/      installer, tray launcher, Bluetooth remote, terminal remote
extension/  one source for the Chrome, Firefox and Safari builds
android/    src/app/fling/*.java, res/, AndroidManifest.xml, build.sh
docs/       guides and these notes
```

## Windows

```powershell
windows\install.cmd                  # from source into %LOCALAPPDATA%\Fling (uv, Python 3.12, PyQt6, catt, adb)
fling quit; fling                    # restart after copying desktop\*.py to %LOCALAPPDATA%\Fling\app
powershell -ExecutionPolicy Bypass -File windows\build.ps1   # dist\Fling-Windows-<ver>.zip (PyInstaller)
```

The standalone build runs catt in-process: `Fling.exe --catt <args>` is the bundled catt CLI.

## Browser extension

```sh
python extension/build.py              # dist/chrome, dist/firefox, dist/safari + zips
./extension/safari/build-safari.sh     # on a Mac with Xcode: Safari app wrapper
```

Load `extension/dist/chrome` unpacked in Chrome/Edge, or `dist/fling-firefox-*.zip` in
`about:debugging`. With the desktop app running the popup shows the TV; `curl
http://127.0.0.1:47613/v1/status` shows what the extension sees.

## Linux

```sh
cd linux && ./install.sh      # packages, catt, venv, launchers, shortcut
fling                          # run (logs: ~/.cache/fling/app.log)
fling quit && fling            # restart after editing ~/.local/share/fling/fling_app.py
```

Quick checks before committing:

```sh
python3 -m py_compile desktop/*.py linux/*.py
bash -n linux/install.sh linux/make-run.sh linux/fling linux/fling-tui
linux/make-run.sh                      # dist/Fling-Setup-<version>-linux.run
```

The Bluetooth service runs on its own: `python3 linux/bt_remote.py serve`, then
`python3 linux/bt_remote.py status` (log: `~/.cache/fling/bt.log`).

## Android

Requirements: a JDK 17 or newer and the Android SDK with `platforms/android-36` and
current build-tools (`ANDROID_HOME`, default `~/Android/Sdk`).

```sh
cd android && ./build.sh       # aapt2 → javac → d8 → zipalign → apksigner (debug key)
adb install -r Fling.apk
```

On Windows the same script runs in Git Bash with a portable JDK 17 and the SDK in
`%LOCALAPPDATA%\Android\Sdk` (found automatically).

An emulator (AVD) is enough for layout and crash checks: `adb logcat -b crash`.
Note that the emulator reaches devices on your network, so it can trigger prompts on
a real TV.

## Testing on a real TV

There is no automated test suite yet. When testing against a TV, remember that several
steps show something on its screen — the pairing code, *Allow debugging?*, Bluetooth
pairing — and that play / pause / stop affect whoever is watching.

## Translations

Every new user-visible string needs English and Russian:

- **Desktop:** strings are written in Russian in `desktop/fling_app.py`; add the English text to
  `EN_PHRASES` in the same file. The built-in guide is `desktop/fling_help.py`.
- **Extension:** `_locales/en/messages.json` and `_locales/ru/messages.json`.
- **Android:** wrap the string in `L.t(...)` and add the pair to `L.java`; labels used
  in the manifest live in `res/values/strings.xml` and `res/values-ru/strings.xml`.

## Releasing

1. Update `CHANGELOG.md` (move items from *Unreleased* to a new version).
   Bump `VERSION` in `desktop/fling_app.py` and `extension/build.py`, and the
   Android version name / code passed to `android/build.sh`.
2. Build what you are shipping and open it: `windows\build.ps1 -Official` for the setup and
   the zip (builds fling-guard and signs `fling.manifest`), `python extension/build.py` for the
   extension zips, `android/build.sh` for the APK (signed with `~/.android/debug.keystore`,
   the key every published APK uses — keep it, or phones can't update).
   Then sign `SHA256SUMS.txt` with `fling-sign` — see
   [SECURITY.md → For the maintainer](../SECURITY.md#for-the-maintainer-releasing).
3. Make sure no personal data is in the code, screenshots or the APK
   (IP addresses, device names, home paths).
4. Attach the setup, the Windows zip, the extension zips, `Fling.apk`, `SHA256SUMS.txt` and
   `SHA256SUMS.txt.minisig` to the GitHub release.
   Write the notes as you would tell a friend what changed. Do not paste the changelog verbatim if it still mentions drafts, models or review tools.
