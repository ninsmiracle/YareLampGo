#!/usr/bin/env bash
# Encode an image as base64 data URI for LLM vision analysis.
#
# Usage: bash analyze.sh <image_path> [--resize WxH]
#
# Returns JSON: {"ok": true, "data_uri": "data:image/jpeg;base64,...", "size": 12345}

set -e

IMAGE="$1"
RESIZE=""

shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --resize) RESIZE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$IMAGE" ] || [ ! -f "$IMAGE" ]; then
    echo '{"ok": false, "error": "Image file not found"}'
    exit 1
fi

# Optional resize using ffmpeg (more portable than sips on Linux)
if [ -n "$RESIZE" ]; then
    RESIZED="/tmp/lampgo_resized_$(basename "$IMAGE")"
    if command -v ffmpeg &>/dev/null; then
        ffmpeg -y -i "$IMAGE" -vf "scale=$RESIZE" "$RESIZED" -loglevel quiet 2>/dev/null
        IMAGE="$RESIZED"
    elif command -v convert &>/dev/null; then
        convert "$IMAGE" -resize "$RESIZE" "$RESIZED"
        IMAGE="$RESIZED"
    fi
fi

# Base64 encode
B64=$(base64 -w 0 "$IMAGE" 2>/dev/null || base64 "$IMAGE" 2>/dev/null)
SIZE=$(wc -c < "$IMAGE" | tr -d ' ')

echo "{\"ok\": true, \"data_uri\": \"data:image/jpeg;base64,${B64}\", \"size\": ${SIZE}}"
