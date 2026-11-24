#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Private macOS build: official Fling sources + keyboard-icon patch + Mac keyboard as the TV remote.
# Must run on macOS 14+ with Swift 6.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/macos/dist"
WORK="${ROOT}/macos/.work"
UPSTREAM_URL="${FLING_UPSTREAM:-https://github.com/tatianathevisionary/fling.git}"
REF="${FLING_REF:-main}"

command -v swift >/dev/null || { echo "swift not found — need macOS with Xcode/Swift"; exit 1; }
uname | grep -q Darwin || { echo "this build only runs on macOS"; exit 1; }

# Sign with the Mac's own "Fling Port Local" identity when it exists: the
# keychain then remembers "Always Allow" across updates (an ad-hoc signature
# changes with every build, so macOS asks for the TV key again each time).
SIGN_ID="$(security find-identity -p codesigning 2>/dev/null | awk '/"Fling Port Local"/ {print $2; exit}')"
SIGN_ID="${SIGN_ID:--}"

rm -rf "$WORK" "$OUT"
# Command Line Tools on macOS 27 ship SwiftUI's @State as a macro without its plugin
# (only full Xcode has it). Fall back to the newest SDK < 27 when Xcode is absent.
if [ -z "${SDKROOT:-}" ] && [[ "$(xcode-select -p)" == *CommandLineTools* ]]; then
  OLD_SDK="$(ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX2[0-6].*.sdk 2>/dev/null | sort -V | tail -1 || true)"
  [ -n "$OLD_SDK" ] && export SDKROOT="$OLD_SDK" && echo "using SDK: $SDKROOT"
fi

mkdir -p "$WORK" "$OUT"
git clone --depth 1 --branch "$REF" "$UPSTREAM_URL" "$WORK/fling"
cp "$ROOT/macos/RemoteView.swift" "$WORK/fling/Sources/Fling/RemoteView.swift"
cp "$ROOT/macos/KeyRemote.swift" "$WORK/fling/Sources/Fling/KeyRemote.swift"
# Mac keyboard → TV while the panel is open: StatusItemController creates KeyRemote
git -C "$WORK/fling" apply "$ROOT/macos/patches/StatusItemController-keyboard.patch"
# FlingKit: TV volume as the TV reports it (+ walking it with keys, like Windows); pairing identity kept in memory, not the keychain
git -C "$WORK/fling" apply "$ROOT/macos/patches/FlingKit.patch"
# Russian / English like the Windows build: L10n.swift + route UI strings through tr()
cp "$ROOT/macos/L10n.swift" "$WORK/fling/Sources/Fling/L10n.swift"
python3 "$ROOT/macos/localize.py" "$WORK/fling/Sources/Fling"

(
  cd "$WORK/fling"
  # Universal binary (arm64 + x86_64), same as upstream publish-bin.sh / installers
  swift build -c release --arch arm64 --arch x86_64 --package-path .
  APP="$WORK/fling/build/Fling.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  # output dir differs between toolchains (.build/apple or .build/out)
  BIN="$(ls .build/apple/Products/Release/Fling .build/out/Products/Release/Fling 2>/dev/null | head -1 || true)"
  [ -n "$BIN" ] || { echo "built binary not found"; exit 1; }
  cp "$BIN" "$APP/Contents/MacOS/Fling"
  # Port-specific plist (own bundle id) — not upstream com.damionrashford.fling
  cp "$ROOT/macos/Info.plist" "$APP/Contents/Info.plist"
  codesign --force --deep --sign "$SIGN_ID" "$APP"
  codesign --verify --verbose "$APP"
  echo "built: $APP"
)

ditto -c -k --sequesterRsrc --keepParent "$WORK/fling/build/Fling.app" "$OUT/Fling.app.zip"
cp -R "$WORK/fling/build/Fling.app" "$OUT/Fling.app"
cp "$WORK/fling/build/Fling.app/Contents/MacOS/Fling" "$OUT/Fling"
cp "$ROOT/macos/Info.plist" "$OUT/Info.plist"
file "$OUT/Fling" || true
ls -lh "$OUT"
echo "OK: $OUT/Fling.app.zip (+ Fling Mach-O + Info.plist for curl-style install)"
