#!/usr/bin/env bash
# Build the Safari version of the Fling extension (run on a Mac with Xcode 15+).
#
#   ./extension/safari/build-safari.sh
#
# 1. builds extension/dist/safari with build.py
# 2. wraps it into an Xcode project (Safari extensions ship inside a small macOS app)
# 3. builds that app unsigned into extension/dist/safari-app/
#
# Then: open the app once, Safari → Settings → Advanced → "Show features for web
# developers", Develop → "Allow Unsigned Extensions", Settings → Extensions → enable Fling.
# The extension needs the Fling desktop app (python desktop/fling_app.py) running on the Mac.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
EXT="$HERE/.."
python3 "$EXT/build.py" safari
OUT="$EXT/dist/safari-xcode"
rm -rf "$OUT"
xcrun safari-web-extension-converter "$EXT/dist/safari" \
  --project-location "$OUT" --app-name "Fling" \
  --bundle-identifier "io.github.orfixe.fling" \
  --macos-only --swift --no-open --no-prompt --force
cd "$OUT/Fling"
xcodebuild -scheme "Fling" -configuration Release \
  CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO \
  -derivedDataPath "$EXT/dist/safari-build" build | tail -3
mkdir -p "$EXT/dist/safari-app"
cp -R "$EXT/dist/safari-build/Build/Products/Release/Fling.app" "$EXT/dist/safari-app/"
echo "==> $EXT/dist/safari-app/Fling.app"
