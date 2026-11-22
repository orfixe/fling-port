# Contributing

Thanks for looking. This is an unofficial port, and a TV someone is watching is not a test device — pairing, ADB and Bluetooth all put a prompt on the screen.

## Getting it running

Linux:

```sh
git clone https://github.com/orfixe/fling-port.git
cd fling-port/linux
./install.sh
```

Windows, from source: double-click `windows\install.cmd`.

Android (needs `ANDROID_HOME`, a JDK 17+, platform android-36):

```sh
cd android
./build.sh
adb install -r Fling.apk
```

The desktop app lives in `desktop/`, not in `linux/`. Linux-only pieces are the installer, the Bluetooth remote and `fling-tui`.

## Before you send a change

- New text needs both languages. Desktop strings are Russian in `desktop/fling_app.py`, English in `EN_PHRASES` in the same file. The guide is `desktop/fling_help.py`. Android pairs go in `L.java` (and `res/values/strings.xml` plus `res/values-ru/strings.xml` for manifest labels). Extension strings go in `extension/src/_locales/en` and `ru`.
- Try it on a real TV if you touched casting, the remote, or discovery. Play, pause, volume and the d-pad are the minimum.
- Don't leave a real IP, a TV name, or a home path in code, screenshots or the APK.
