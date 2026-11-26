#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
#
# Builds Fling for Android with Gradle (AndroidX + Material, R8):
#   android/Fling.apk       the GitHub APK (flavor github): self-update, signed here with
#                           apksigner key rotation — the old key for Android 8 (v1/v2), the
#                           release key + lineage for Android 9+ (v3.1) — so phones that have
#                           Fling installed keep updating it
#   android/Fling-play.aab  the Google Play bundle (flavor play): no self-update, signed by
#                           Gradle with the release key as the upload key
# The release key comes only from the environment, set by windows/android-key.ps1 -Build:
#   FLING_ANDROID_KS, FLING_ANDROID_KS_PASS, FLING_ANDROID_LINEAGE, FLING_ANDROID_OLD_KS
# Without it, the APK is signed with the old key only (~/.android/debug.keystore) and no
# Play bundle is made. Works on Linux and on Windows (Git Bash).
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
if [ ! -d "$SDK" ] && [ -n "${LOCALAPPDATA:-}" ]; then SDK="$LOCALAPPDATA/Android/Sdk"; fi
export ANDROID_HOME="$SDK"
if [ -z "${JAVA_HOME:-}" ] && [ -n "${LOCALAPPDATA:-}" ] && [ -d "$LOCALAPPDATA/Android/jdk-17.0.21.1+2" ]; then
    export JAVA_HOME="$LOCALAPPDATA/Android/jdk-17.0.21.1+2"
fi
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | sort -V | tail -1)"
tool() {   # build-tools binary: name, name.exe or name.bat
    for f in "$BT/$1" "$BT/$1.exe" "$BT/$1.bat"; do [ -f "$f" ] && { echo "$f"; return; }; done
    echo "missing build tool: $1" >&2; exit 1
}
ZIPALIGN=$(tool zipalign); APKSIGNER=$(tool apksigner)
OLD_KS="${FLING_ANDROID_OLD_KS:-$HOME/.android/debug.keystore}"
[ -f "$OLD_KS" ] || { echo "the old signing key is missing: $OLD_KS" >&2; exit 1; }
RELEASE=0
if [ -n "${FLING_ANDROID_KS:-}" ] && [ -n "${FLING_ANDROID_KS_PASS:-}" ] && [ -n "${FLING_ANDROID_LINEAGE:-}" ]; then
    RELEASE=1
fi

echo "==> gradle"
TASKS=(assembleGithubRelease)
[ "$RELEASE" = 1 ] && TASKS+=(bundlePlayRelease)
./gradlew --no-daemon -q "${TASKS[@]}"

echo "==> sign"
UNSIGNED=build/outputs/apk/github/release/fling-android-github-release-unsigned.apk
"$ZIPALIGN" -f -p 4 "$UNSIGNED" build/aligned.apk
if [ "$RELEASE" = 1 ]; then
    "$APKSIGNER" sign \
        --ks "$OLD_KS" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android \
        --next-signer \
        --ks "$FLING_ANDROID_KS" --ks-key-alias fling --ks-pass env:FLING_ANDROID_KS_PASS --key-pass env:FLING_ANDROID_KS_PASS \
        --lineage "$FLING_ANDROID_LINEAGE" --rotation-min-sdk-version 28 \
        --out Fling.apk build/aligned.apk
    cp build/outputs/bundle/playRelease/*.aab Fling-play.aab
else
    echo "    (no release key in the environment: the old key only — use windows/android-key.ps1 -Build)"
    "$APKSIGNER" sign --ks "$OLD_KS" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android \
        --out Fling.apk build/aligned.apk
fi
"$APKSIGNER" verify Fling.apk
ls -la Fling.apk
[ "$RELEASE" = 1 ] && ls -la Fling-play.aab || true
