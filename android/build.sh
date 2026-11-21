#!/usr/bin/env bash
# Builds Fling.apk without Gradle: aapt2 → javac → d8 → zipalign → apksigner.
set -euo pipefail
cd "$(dirname "$0")"
SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | sort -V | tail -1)"
JAR="$SDK/platforms/android-36/android.jar"
OUT=build
rm -rf "$OUT"; mkdir -p "$OUT/res" "$OUT/classes" "$OUT/gen"

echo "==> resources"
"$BT/aapt2" compile --dir res -o "$OUT/res/res.zip"
"$BT/aapt2" link -I "$JAR" --manifest AndroidManifest.xml \
    --min-sdk-version 26 --target-sdk-version 36 \
    --version-code "${VERSION_CODE:-1}" --version-name "${VERSION_NAME:-1.0}" \
    --java "$OUT/gen" -o "$OUT/base.apk" "$OUT/res/res.zip"

echo "==> javac"
find src "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac --release 17 -nowarn -encoding UTF-8 -Xlint:-options \
    -cp "$JAR" -d "$OUT/classes" @"$OUT/sources.txt"

echo "==> d8"
"$BT/d8" --release --min-api 26 --lib "$JAR" --output "$OUT" $(find "$OUT/classes" -name '*.class')

echo "==> package"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT" && zip -q -j unsigned.apk classes.dex)
"$BT/zipalign" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
KS="$HOME/.android/debug.keystore"
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --out Fling.apk "$OUT/aligned.apk"
"$BT/apksigner" verify Fling.apk
ls -la Fling.apk
