#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APK="$ROOT/build/lampgo-camera-companion-debug.apk"

if [[ ! -f "$APK" ]]; then
  "$ROOT/scripts/build.sh"
fi

adb install --no-streaming -r -g "$APK"
adb shell pm grant com.lampgo.camera android.permission.CAMERA || true
adb shell pm grant com.lampgo.camera android.permission.POST_NOTIFICATIONS || true
adb shell am start -n com.lampgo.camera/.MainActivity
adb forward tcp:18765 tcp:8765

echo "Lampgo camera URL: http://127.0.0.1:18765/snapshot.jpg"
