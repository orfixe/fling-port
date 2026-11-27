#!/usr/bin/env bash
# Dev loop: incremental github debug build -> adb install -r -> launch [-> logcat of the app].
#   ./dev.sh            build, install, launch
#   ./dev.sh --logs     ... then follow logcat for the app's pid (Ctrl-C to stop)
# Device: the single connected one, or $ANDROID_SERIAL.
set -euo pipefail
cd "$(dirname "$0")"
export ANDROID_HOME="${ANDROID_HOME:-$LOCALAPPDATA/Android/Sdk}"
export JAVA_HOME="${JAVA_HOME:-$LOCALAPPDATA/Android/jdk-17.0.21.1+2}"
ADB="$ANDROID_HOME/platform-tools/adb"
PKG=app.fling

./gradlew -q assembleGithubDebug
"$ADB" install -r build/outputs/apk/github/debug/fling-android-github-debug.apk
"$ADB" shell am start -n "$PKG/.MainActivity"

if [ "${1:-}" = --logs ]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        PID=$("$ADB" shell pidof -s "$PKG" | tr -d '\r') && [ -n "$PID" ] && break
        sleep 0.5
    done
    [ -n "${PID:-}" ] || { echo "app not running" >&2; exit 1; }
    "$ADB" logcat -v color --pid="$PID"
fi
