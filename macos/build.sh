#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Private macOS build: official Fling sources with this port's changes (macos/Sources/) laid over them.
# Must run on macOS 14+ with Swift 6.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/macos/dist"
WORK="${ROOT}/macos/.work"
UPSTREAM_URL="${FLING_UPSTREAM:-https://github.com/tatianathevisionary/fling.git}"

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
# Upstream Fling pinned to macos/UPSTREAM_REF (FLING_REF overrides it). Our changes live in
# macos/Sources/ as whole files laid over the upstream tree; macos/upstream-base/ keeps the
# upstream version each changed file was taken from, so ./macos/update-upstream.sh can
# three-way merge a newer upstream into them.
REF="${FLING_REF:-$(cat "$ROOT/macos/UPSTREAM_REF")}"
git clone --quiet "$UPSTREAM_URL" "$WORK/fling"
git -C "$WORK/fling" checkout --quiet "$REF"
cp -R "$ROOT/macos/Sources/." "$WORK/fling/Sources/"
# Port's own unit tests (cd macos/.work/fling && swift test)
if [ -d "$ROOT/macos/Tests" ]; then cp -R "$ROOT/macos/Tests/." "$WORK/fling/Tests/"; fi

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
