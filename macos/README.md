# Private macOS build (keyboard icon)

Unofficial build of Fling for macOS with the keyboard-icon `RemoteView` change, and the
Mac keyboard as the TV remote while the panel is open (`KeyRemote.swift`).
Fling is by Damion Rashford and Tatiana, see [CREDITS.md](../CREDITS.md).
This build is not affiliated with them.

## Why Safari deletes the zip

Browser downloads get a **quarantine** flag. Ad-hoc-signed apps (same as upstream
Fling without Developer ID) are often blocked or removed as “malware”. Upstream
avoids this by installing via `curl` / Terminal — we do the same.

## Install on a Mac (recommended)

```sh
# once: gh auth login   (account that can read the private orfixe/fling-port)
curl -fsSL -H "Authorization: token $(gh auth token)" \
  "https://raw.githubusercontent.com/orfixe/fling-port/main/macos/install.sh" \
  -o /tmp/install-fling-port.sh
bash /tmp/install-fling-port.sh
```

Or from a clone:

```sh
./macos/install.sh
```

Installs to `~/Applications/Fling Port.app`, re-signs locally, strips quarantine.

## Build on a Mac

```sh
./macos/build.sh
# → macos/dist/Fling.app.zip + Fling + Info.plist
```

CI: `.github/workflows/macos-fling.yml`.
