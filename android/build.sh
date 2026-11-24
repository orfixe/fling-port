#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
# Builds Fling.apk without Gradle: aapt2 → javac → d8 → zipalign → apksigner.
# Works on Linux and on Windows (Git Bash): tools are found with or without .exe/.bat.
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
if [ ! -d "$SDK" ] && [ -n "${LOCALAPPDATA:-}" ]; then SDK="$LOCALAPPDATA/Android/Sdk"; fi
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | sort -V | tail -1)"
tool() {   # build-tools binary: name, name.exe or name.bat
    for f in "$BT/$1" "$BT/$1.exe" "$BT/$1.bat"; do [ -f "$f" ] && { echo "$f"; return; }; done
    echo "missing build tool: $1" >&2; exit 1
}
AAPT2=$(tool aapt2); D8=$(tool d8); ZIPALIGN=$(tool zipalign); APKSIGNER=$(tool apksigner)
JAR="$SDK/platforms/android-36/android.jar"
OUT=build
rm -rf "$OUT"; mkdir -p "$OUT/res" "$OUT/classes" "$OUT/gen"

echo "==> resources"
"$AAPT2" compile --dir res -o "$OUT/res/res.zip"
"$AAPT2" link -I "$JAR" --manifest AndroidManifest.xml \
    --min-sdk-version 26 --target-sdk-version 36 \
    --version-code "${VERSION_CODE:-5}" --version-name "${VERSION_NAME:-0.3.2}" \
    --java "$OUT/gen" -o "$OUT/base.apk" "$OUT/res/res.zip"

echo "==> javac"
find src "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac --release 17 -nowarn -encoding UTF-8 -Xlint:-options \
    -cp "$JAR" -d "$OUT/classes" @"$OUT/sources.txt"

echo "==> d8"
(cd "$OUT/classes" && jar cf ../classes.jar .)   # one jar: no command-line length limit on Windows
"$D8" --release --min-api 26 --lib "$JAR" --output "$OUT" "$OUT/classes.jar"

echo "==> package"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
if command -v zip >/dev/null; then
    (cd "$OUT" && zip -q -j unsigned.apk classes.dex)
else   # Git Bash has no zip: the JDK's jar tool adds the file without a manifest
    (cd "$OUT" && jar -u -M -f unsigned.apk classes.dex)
fi
"$ZIPALIGN" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
KS="$HOME/.android/debug.keystore"
if [ ! -f "$KS" ]; then   # same parameters as Android Studio's debug key
    mkdir -p "$(dirname "$KS")"
    keytool -genkeypair -keystore "$KS" -storepass android -keypass android -alias androiddebugkey         -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
fi
"$APKSIGNER" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --out Fling.apk "$OUT/aligned.apk"
"$APKSIGNER" verify Fling.apk
ls -la Fling.apk
