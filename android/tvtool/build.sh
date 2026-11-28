#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
#
# Builds tvtool/Icons.java into assets/tv-icons.dex: the tiny program Fling for Android pushes
# to the TV over ADB (/data/local/tmp) and runs with app_process, to read the real names and
# icons of the TV's apps for the app tiles. Rebuild after changing Icons.java; the dex is
# committed so a normal build needs nothing extra.
set -euo pipefail
cd "$(dirname "$0")/.."
SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
if [ ! -d "$SDK" ] && [ -n "${LOCALAPPDATA:-}" ]; then SDK="$LOCALAPPDATA/Android/Sdk"; fi
if [ -z "${JAVA_HOME:-}" ] && [ -n "${LOCALAPPDATA:-}" ] && [ -d "$LOCALAPPDATA/Android/jdk-17.0.21.1+2" ]; then
    export JAVA_HOME="$LOCALAPPDATA/Android/jdk-17.0.21.1+2"
fi
JAVAC="${JAVA_HOME:+$JAVA_HOME/bin/}javac"
PLATFORM=$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | sort -V | tail -1)"
D8="$BT/d8"; [ -f "$D8" ] || D8="$BT/d8.bat"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
"$JAVAC" --release 8 -nowarn -cp "$PLATFORM/android.jar" -d "$tmp/classes" tvtool/Icons.java
"$D8" --min-api 26 --output "$tmp" "$tmp/classes/app/fling/tv/Icons.class"
mkdir -p assets
cp "$tmp/classes.dex" assets/tv-icons.dex
echo "assets/tv-icons.dex: $(wc -c < assets/tv-icons.dex) bytes"
