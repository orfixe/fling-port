#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Install for the unofficial macOS Fling Port build.
#
# Do NOT download Fling.app.zip in Safari/Chrome — macOS quarantines browser
# downloads and often removes ad-hoc-signed apps as “malware”.
# This script pulls the Mach-O with curl (no quarantine) and assembles the
# .app locally, same approach as upstream get-fling.sh.
#
# Every downloaded file is checked against the release's SHA256SUMS.txt, whose signature
# (SHA256SUMS.txt.minisig) must verify with the Fling release key below — see SECURITY.md.
# The check uses `minisign` (installed with Homebrew when missing), or an already
# installed, verified Fling Port (`Fling --verify-release`). Nothing is installed if the
# files don't match.
#
# Needs: macOS 14+. While the repository is private, `gh` logged into an account
# that can read it (the public download is tried first).
#
# Environment:
#   FLING_PORT_TAG=v0.5.9         a given release instead of the latest
#   FLING_PORT_APP=…              install somewhere else than ~/Applications/Fling Port.app
#   FLING_PORT_FROM_DIR=…         take the release files from a folder instead of downloading
#   FLING_PORT_UNVERIFIED=1       skip the signature check (your own / unofficial build).
#                                 Fling Port then runs its guard in report-only mode.
#   FLING_PORT_ALLOW_DOWNGRADE=1  allow installing a release older than the installed one
#   FLING_PORT_NO_LAUNCH=1        don't start Fling Port at the end
set -euo pipefail

REPO="${FLING_PORT_REPO:-orfixe/fling-port}"
TAG="${FLING_PORT_TAG:-latest}"
APP="${FLING_PORT_APP:-$HOME/Applications/Fling Port.app}"
# The release public key (minisign, Ed25519) — the same as SECURITY.md and security/fling-release.pub.
RELEASE_KEY="RWRm6sdJduEyJc2SZt5ee5tDKKaWvAoJcDttUeHJh1wve7upw8c0p5AJ"
RECORD="$HOME/Library/Application Support/Fling Port/install.plist"

uname | grep -q Darwin || { echo "Run this on a Mac."; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

refuse() {
  echo ""
  echo "STOP: $1"
  echo "Nothing was installed. The files you got are not the ones signed for this release:"
  echo "they may have been damaged on the way or replaced. Try again later; if it keeps"
  echo "happening, please report it (SECURITY.md → Report a vulnerability)."
  exit 1
}

# ---------------------------------------------------------------- download
if [ -n "${FLING_PORT_FROM_DIR:-}" ]; then
  echo "==> Taking release files from $FLING_PORT_FROM_DIR"
  for f in Fling Info.plist Fling.app.zip SHA256SUMS.txt SHA256SUMS.txt.minisig; do
    [ -f "$FLING_PORT_FROM_DIR/$f" ] && cp "$FLING_PORT_FROM_DIR/$f" "$TMP/$f"
  done
  [ -f "$TMP/Fling" ] && [ -f "$TMP/Info.plist" ] || [ -f "$TMP/Fling.app.zip" ] \
    || { echo "$FLING_PORT_FROM_DIR has neither Fling + Info.plist nor Fling.app.zip"; exit 1; }
else
  echo "==> Downloading release assets ($REPO @ $TAG)"
  if [ "$TAG" = latest ]; then BASE="https://github.com/$REPO/releases/latest/download"
  else BASE="https://github.com/$REPO/releases/download/$TAG"; fi
  # Public repository: plain HTTPS. Private: gh. Prefer raw Mach-O + plist; fall back to zip.
  if curl -fsSL "$BASE/Fling" -o "$TMP/Fling" && curl -fsSL "$BASE/Info.plist" -o "$TMP/Info.plist"; then
    curl -fsSL "$BASE/SHA256SUMS.txt" -o "$TMP/SHA256SUMS.txt" || true
    curl -fsSL "$BASE/SHA256SUMS.txt.minisig" -o "$TMP/SHA256SUMS.txt.minisig" || true
  elif ! command -v gh >/dev/null; then
    echo "Could not download $BASE/Fling. If the repository is private, install GitHub CLI"
    echo "(https://cli.github.com/), run: gh auth login, and try again."
    exit 1
  elif gh release download "$TAG" -R "$REPO" -p 'Fling' -p 'Info.plist' \
         -p 'SHA256SUMS.txt' -p 'SHA256SUMS.txt.minisig' -D "$TMP" --clobber 2>/dev/null \
       && [ -f "$TMP/Fling" ]; then
    :
  elif gh release download "$TAG" -R "$REPO" -p 'Fling.app.zip' \
         -p 'SHA256SUMS.txt' -p 'SHA256SUMS.txt.minisig' -D "$TMP" --clobber; then
    :
  else
    echo "Could not download release assets. Check: gh auth status && gh release list -R $REPO"
    exit 1
  fi
fi

# ---------------------------------------------------------------- verify
# A verified Fling Port already installed may check the list itself: its code signature,
# its install receipt and the recorded hash of its program must all still be right.
installed_verifier() {
  local exe="$APP/Contents/MacOS/Fling" receipt="$APP/Contents/Resources/install-receipt.plist"
  [ -x "$exe" ] && [ -f "$receipt" ] && [ -f "$RECORD" ] || return 1
  grep -q -- '--verify-release' "$exe" || return 1   # a Fling Port that has the check at all
  [ "$(plutil -extract verified raw -o - "$receipt" 2>/dev/null)" = true ] || return 1
  codesign --verify --strict "$APP" 2>/dev/null || return 1
  local want got
  want="$(plutil -extract binarySHA256 raw -o - "$RECORD" 2>/dev/null)" || return 1
  got="$(shasum -a 256 "$exe" | awk '{print $1}')"
  [ -n "$want" ] && [ "$want" = "$got" ] || return 1
  "$exe" --guard-selfcheck >/dev/null 2>&1 || return 1
  echo "$exe"
}

VERSION=""
VERIFIED=false
if [ "${FLING_PORT_UNVERIFIED:-}" = 1 ]; then
  echo "==> FLING_PORT_UNVERIFIED=1: NOT checking the release signature."
  echo "    Only do this for a build you made yourself. Fling Port's guard will only report."
else
  echo "==> Checking the release signature (Fling release key ${RELEASE_KEY:0:12}…)"
  [ -s "$TMP/SHA256SUMS.txt" ] && [ -s "$TMP/SHA256SUMS.txt.minisig" ] \
    || refuse "the release has no signed file list (SHA256SUMS.txt + .minisig)."
  if ! command -v minisign >/dev/null && ! installed_verifier >/dev/null && command -v brew >/dev/null; then
    echo "==> Installing minisign (the signature checker) with Homebrew"
    brew install minisign >/dev/null || true
  fi
  if command -v minisign >/dev/null; then
    OUT="$(minisign -V -m "$TMP/SHA256SUMS.txt" -x "$TMP/SHA256SUMS.txt.minisig" -P "$RELEASE_KEY" 2>&1)" \
      || refuse "SHA256SUMS.txt is not signed with the Fling release key (minisign: ${OUT##*$'\n'})."
    TC="$(printf '%s\n' "$OUT" | sed -n 's/^Trusted comment: //p')"
    case "$TC" in fling-release*) ;; *) refuse "the signed list is not a Fling release list ($TC)." ;; esac
    VERSION="$(printf '%s\n' "$TC" | tr ' ' '\n' | sed -n 's/^version=//p')"
  elif VERIFIER="$(installed_verifier)"; then
    OUT="$("$VERIFIER" --verify-release "$TMP/SHA256SUMS.txt" "$TMP/SHA256SUMS.txt.minisig" 2>&1)" \
      || refuse "SHA256SUMS.txt is not signed with the Fling release key ($OUT)."
    VERSION="$(printf '%s\n' "$OUT" | sed -n 's/^OK version=//p')"
  else
    echo ""
    echo "Can't check the release signature: minisign is not installed."
    echo "Install it (brew install minisign — https://jedisct1.github.io/minisign/) and run this again."
    exit 1
  fi
  # each downloaded file must be on the signed list with the same SHA-256
  for f in Fling Info.plist Fling.app.zip; do
    [ -f "$TMP/$f" ] || continue
    want="$(awk -v n="$f" '{ name = $2; sub(/^\*/, "", name) } name == n { print tolower($1); exit }' "$TMP/SHA256SUMS.txt")"
    got="$(shasum -a 256 "$TMP/$f" | awk '{print $1}')"
    [ -n "$want" ] || refuse "$f is not on the signed list."
    [ "$want" = "$got" ] || refuse "$f does not match the signed list (SHA-256 $got, expected $want)."
  done
  if [ "$TAG" != latest ] && [ -n "$VERSION" ] && [ "${TAG#v}" != "$VERSION" ]; then
    refuse "you asked for $TAG, but the signed list is for version $VERSION."
  fi
  # no silent downgrade to an older (signed, but possibly vulnerable) release
  OLD="$(plutil -extract version raw -o - "$APP/Contents/Resources/install-receipt.plist" 2>/dev/null || true)"
  if [ -n "$OLD" ] && [ -n "$VERSION" ] && [ "${FLING_PORT_ALLOW_DOWNGRADE:-}" != 1 ] \
     && [ "$(printf '%s\n%s\n' "$OLD" "$VERSION" | sort -V | head -1)" = "$VERSION" ] && [ "$OLD" != "$VERSION" ]; then
    echo "Installed: $OLD, this release: $VERSION — not downgrading (FLING_PORT_ALLOW_DOWNGRADE=1 to do it anyway)."
    exit 1
  fi
  VERIFIED=true
  echo "==> OK: signed release ${VERSION:-?}, files match"
fi

if [ ! -f "$TMP/Fling" ]; then
  echo "==> Unpacking zip (still assembled + re-signed locally)"
  ditto -x -k "$TMP/Fling.app.zip" "$TMP/unpacked"
  BIN="$(find "$TMP/unpacked" -type f -path '*/Contents/MacOS/Fling' | head -1)"
  PLIST="$(find "$TMP/unpacked" -type f -path '*/Contents/Info.plist' | head -1)"
  cp "$BIN" "$TMP/Fling"
  cp "$PLIST" "$TMP/Info.plist"
fi
RELEASE_SHA="$(shasum -a 256 "$TMP/Fling" | awk '{print $1}')"

chmod +x "$TMP/Fling"
# Strip any quarantine that sneaked in
xattr -cr "$TMP/Fling" "$TMP/Info.plist" 2>/dev/null || true

# ---------------------------------------------------------------- install
echo "==> Installing to $APP"
mkdir -p "$(dirname "$APP")"
# only the copy being replaced
pkill -f "$APP/Contents/MacOS/Fling" 2>/dev/null || true
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv "$TMP/Fling" "$APP/Contents/MacOS/Fling"
mv "$TMP/Info.plist" "$APP/Contents/Info.plist"
if [ "$VERIFIED" = true ]; then
  # sealed by the signature below; tells Fling Port's guard to enforce (SECURITY.md)
  cat > "$APP/Contents/Resources/install-receipt.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>verified</key><true/>
    <key>version</key><string>${VERSION}</string>
    <key>releaseSHA256</key><string>${RELEASE_SHA}</string>
    <key>installedAt</key><string>$(date -u +%Y-%m-%dT%H:%M:%SZ)</string>
</dict>
</plist>
EOF
fi
# Hardened runtime, same exceptions as macos/Fling.entitlements: microphone (voice search),
# Apple Events (reading the browser's tab).
cat > "$TMP/Fling.entitlements" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.device.audio-input</key><true/>
    <key>com.apple.security.automation.apple-events</key><true/>
</dict>
</plist>
EOF
# Sign with the Mac's own "Fling Port Local" identity when it exists: the
# keychain then remembers "Always Allow" across updates (an ad-hoc signature
# changes with every build, so macOS asks for the TV key again each time).
SIGN_ID="$(security find-identity -p codesigning 2>/dev/null | awk '/"Fling Port Local"/ {print $2; exit}')"
SIGN_ID="${SIGN_ID:--}"
codesign --force --deep --options runtime --entitlements "$TMP/Fling.entitlements" -s "$SIGN_ID" "$APP"
xattr -cr "$APP" 2>/dev/null || true
codesign --verify --strict "$APP"

# The hash of the signed program, outside the bundle (a sealed file can't hold the hash of
# what seals it). Fling Port compares it at every start.
if [ "$VERIFIED" = true ]; then
  mkdir -p "$(dirname "$RECORD")"
  cat > "$RECORD" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>binarySHA256</key><string>$(shasum -a 256 "$APP/Contents/MacOS/Fling" | awk '{print $1}')</string>
    <key>app</key><string>${APP//&/&amp;}</string>
</dict>
</plist>
EOF
  chmod 600 "$RECORD"
else
  rm -f "$RECORD"   # a record of an earlier verified install would not match this one
fi

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

if [ "${FLING_PORT_NO_LAUNCH:-}" != 1 ]; then
  echo "==> Launching"
  open "$APP"
fi
echo ""
echo "Done — look for the cast icon in the menu bar."
if [ "$VERIFIED" = true ]; then
  echo "Verified: signed release ${VERSION:-?}. Fling Port will ask once before it runs catt,"
  echo "uv or other tools that aren't signed by Apple or a known developer."
fi
echo "This is an unofficial port (CREDITS.md): original code Damion Rashford,"
echo "original repo Tatiana. Not affiliated with them."
