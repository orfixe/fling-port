#!/usr/bin/env bash
# Private install for the unofficial macOS Fling Port build.
#
# Do NOT download Fling.app.zip in Safari/Chrome — macOS quarantines browser
# downloads and often removes ad-hoc-signed apps as “malware”.
# This script pulls the Mach-O with `gh` (no quarantine) and assembles the
# .app locally, same approach as upstream get-fling.sh.
#
# Needs: macOS 14+, `gh` logged into a account that can read orfixe/fling-port.
set -euo pipefail

REPO="${FLING_PORT_REPO:-orfixe/fling-port}"
TAG="${FLING_PORT_TAG:-latest}"
APP="${FLING_PORT_APP:-$HOME/Applications/Fling Port.app}"

command -v gh >/dev/null || {
  echo "Install GitHub CLI first: https://cli.github.com/  then: gh auth login"
  exit 1
}
uname | grep -q Darwin || { echo "Run this on a Mac."; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> Downloading private release assets ($REPO @ $TAG)"
# Prefer raw Mach-O + plist; fall back to zip if needed
if gh release download "$TAG" -R "$REPO" -p 'Fling' -p 'Info.plist' -D "$TMP" 2>/dev/null; then
  :
elif gh release download "$TAG" -R "$REPO" -p 'Fling.app.zip' -D "$TMP"; then
  echo "==> Unpacking zip (still assembled + re-signed locally)"
  ditto -x -k "$TMP/Fling.app.zip" "$TMP/unpacked"
  BIN="$(find "$TMP/unpacked" -type f -path '*/Contents/MacOS/Fling' | head -1)"
  PLIST="$(find "$TMP/unpacked" -type f -name Info.plist | head -1)"
  cp "$BIN" "$TMP/Fling"
  cp "$PLIST" "$TMP/Info.plist"
else
  echo "Could not download release assets. Check: gh auth status && gh release list -R $REPO"
  exit 1
fi

chmod +x "$TMP/Fling"
# Strip any quarantine that sneaked in
xattr -cr "$TMP/Fling" "$TMP/Info.plist" 2>/dev/null || true

echo "==> Installing to $APP"
mkdir -p "$(dirname "$APP")"
killall "Fling Port" Fling 2>/dev/null || true
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv "$TMP/Fling" "$APP/Contents/MacOS/Fling"
mv "$TMP/Info.plist" "$APP/Contents/Info.plist"
# Fresh local ad-hoc signature — accepted by macOS; no foreign quarantine
codesign --force --deep -s - "$APP"
xattr -cr "$APP" 2>/dev/null || true

if [ ! -x "$HOME/.local/bin/catt" ] && ! command -v catt >/dev/null 2>&1; then
  if [ ! -x "$HOME/.local/bin/uv" ] && ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv"
    curl -fsSL https://astral.sh/uv/install.sh -o "$TMP/uv-install.sh"
    sh "$TMP/uv-install.sh"
  fi
  echo "==> Installing catt (Cast engine)"
  UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"
  "$UV_BIN" tool install catt
fi

echo "==> Launching"
open "$APP"
echo ""
echo "Done — look for the cast icon in the menu bar."
echo "This is an unofficial port (CREDITS.md): original code Damion Rashford,"
echo "original repo Tatiana. Not affiliated with them."
