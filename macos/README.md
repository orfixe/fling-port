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

## What this build adds to upstream Fling

- the **browser extension** bridge (`127.0.0.1:47613`, same protocol as Windows / Linux) —
  `Sources/FlingKit/BrowserBridge.swift`, handler in `Sources/Fling/PortServices.swift`. The
  unpacked Chrome / Firefox builds of `extension/` ship in `Fling.app/Contents/Resources/extension`;
  *Settings → Casting → Show extension folder* copies them to
  `~/Library/Application Support/Fling Port/extension` to load from there
  (Chrome / Edge / Brave: Developer mode → Load unpacked; Firefox: about:debugging → Load
  Temporary Add-on). The extension talks HTTP to the app, so there is no native-messaging
  manifest to install. Safari: `extension/safari/build-safari.sh` needs full Xcode
  (`safari-web-extension-converter`), which the Command Line Tools don't have.
- **⌃⌥⌘C** casts the current tab from anywhere (Carbon hot key, no Accessibility permission);
- files / folders / m3u / pls as a **queue**, **photo slideshows**, subtitles, resume
  (`MediaFiles.swift`, `MediaQueue.swift` — one lasting `catt cast` per item);
- "Now on TV" **notifications** when the video / track changes; a **Settings** window.

Tests: `macos/Tests/` (swift-testing). With the Command Line Tools only (no Xcode) upstream's
XCTest suite can't build; run the port's tests against a package of `FlingKit` with
`SDKROOT=<MacOSX26.x.sdk> swift test -Xswiftc -plugin-path -Xswiftc
/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing`.

## Layout

- `UPSTREAM_REF` — the upstream Fling commit the build uses.
- `Sources/` — this port's files, laid over upstream `Sources/` (changed copies + new files).
- `upstream-base/` — upstream's version of each changed file, for merging a newer upstream.
- `update-upstream.sh [ref]` — three-way merges upstream `ref` (default `main`) into `Sources/`.

## Build on a Mac

```sh
./macos/build.sh
# → macos/dist/Fling.app.zip + Fling + Info.plist
```

CI: `.github/workflows/macos-fling.yml`.
