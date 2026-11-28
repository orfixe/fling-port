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
curl -fsSL https://raw.githubusercontent.com/orfixe/fling-port/main/macos/install.sh \
  -o /tmp/install-fling-port.sh
bash /tmp/install-fling-port.sh
```

Or from a clone:

```sh
./macos/install.sh
```

Installs to `~/Applications/Fling Port.app`, re-signs locally, strips quarantine.

## Layout

- `UPSTREAM_REF` — the upstream Fling commit the build uses.
- `Sources/` — this port's files, laid over upstream `Sources/` (changed copies + new files).
- `upstream-base/` — upstream's version of each changed file, for merging a newer upstream.
- `Sources/FlingKit/Samsung/` — Smart View for Samsung TVs (remote, apps, DLNA, SSDP, Wake-on-LAN);
  see [docs/smart-view.md](../docs/smart-view.md#on-a-mac).
- `Tests/` — the port's tests (Swift Testing), laid over upstream `Tests/`.
- `update-upstream.sh [ref]` — three-way merges upstream `ref` (default `main`) into `Sources/`.

## Build on a Mac

```sh
./macos/build.sh
# → macos/dist/Fling.app.zip + Fling + Info.plist
```

CI: `.github/workflows/macos-fling.yml`.
