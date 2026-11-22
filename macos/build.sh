#!/usr/bin/env bash
# Private macOS build: official Fling sources + keyboard-icon patch.
# Must run on macOS 14+ with Swift 6.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/macos/dist"
WORK="${ROOT}/macos/.work"
UPSTREAM_URL="${FLING_UPSTREAM:-https://github.com/tatianathevisionary/fling.git}"
REF="${FLING_REF:-main}"

command -v swift >/dev/null || { echo "swift not found — need macOS with Xcode/Swift"; exit 1; }
uname | grep -q Darwin || { echo "this build only runs on macOS"; exit 1; }

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$OUT"
git clone --depth 1 --branch "$REF" "$UPSTREAM_URL" "$WORK/fling"
cp "$ROOT/macos/RemoteView.swift" "$WORK/fling/Sources/Fling/RemoteView.swift"

(
  cd "$WORK/fling"
  # Universal binary (arm64 + x86_64), same as upstream publish-bin.sh / installers
  swift build -c release --arch arm64 --arch x86_64 --package-path .
  APP="$WORK/fling/build/Fling.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp .build/apple/Products/Release/Fling "$APP/Contents/MacOS/Fling"
  # Port-specific plist (own bundle id) — not upstream com.damionrashford.fling
  cp "$ROOT/macos/Info.plist" "$APP/Contents/Info.plist"
  codesign --force --deep --sign - "$APP"
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
