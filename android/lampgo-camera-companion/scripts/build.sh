#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK_ROOT="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
BUILD_TOOLS_VERSION="${ANDROID_BUILD_TOOLS_VERSION:-35.0.1}"
PLATFORM_VERSION="${ANDROID_PLATFORM_VERSION:-android-35}"
BUILD_TOOLS="$SDK_ROOT/build-tools/$BUILD_TOOLS_VERSION"
ANDROID_JAR="$SDK_ROOT/platforms/$PLATFORM_VERSION/android.jar"
BUILD_DIR="$ROOT/build"
OUT_APK="$BUILD_DIR/lampgo-camera-companion-debug.apk"

if [[ ! -f "$ANDROID_JAR" ]]; then
  echo "Missing $ANDROID_JAR" >&2
  exit 1
fi

for tool in aapt2 d8 apksigner zipalign; do
  if [[ ! -x "$BUILD_TOOLS/$tool" ]]; then
    echo "Missing $BUILD_TOOLS/$tool" >&2
    exit 1
  fi
done

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/compiled" "$BUILD_DIR/generated" "$BUILD_DIR/classes" "$BUILD_DIR/dex"

"$BUILD_TOOLS/aapt2" compile --dir "$ROOT/app/src/main/res" -o "$BUILD_DIR/compiled/resources.zip"
"$BUILD_TOOLS/aapt2" link \
  -o "$BUILD_DIR/unsigned-res.apk" \
  -I "$ANDROID_JAR" \
  --manifest "$ROOT/app/src/main/AndroidManifest.xml" \
  --java "$BUILD_DIR/generated" \
  "$BUILD_DIR/compiled/resources.zip"

find "$ROOT/app/src/main/java" "$BUILD_DIR/generated" -name '*.java' | sort > "$BUILD_DIR/java_sources.txt"
javac -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -d "$BUILD_DIR/classes" @"$BUILD_DIR/java_sources.txt"

"$BUILD_TOOLS/d8" --lib "$ANDROID_JAR" --output "$BUILD_DIR/dex" $(find "$BUILD_DIR/classes" -name '*.class' | sort)
cp "$BUILD_DIR/unsigned-res.apk" "$BUILD_DIR/unsigned.apk"
(cd "$BUILD_DIR/dex" && zip -qr "$BUILD_DIR/unsigned.apk" classes.dex)

KEYSTORE="$BUILD_DIR/debug.keystore"
keytool -genkeypair \
  -keystore "$KEYSTORE" \
  -storepass android \
  -keypass android \
  -alias androiddebugkey \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000 \
  -dname "CN=Android Debug,O=Lampgo,C=US" >/dev/null

"$BUILD_TOOLS/zipalign" -f -p 4 "$BUILD_DIR/unsigned.apk" "$BUILD_DIR/aligned.apk"
"$BUILD_TOOLS/apksigner" sign \
  --ks "$KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$OUT_APK" \
  "$BUILD_DIR/aligned.apk"
"$BUILD_TOOLS/apksigner" verify --verbose "$OUT_APK"

echo "$OUT_APK"
