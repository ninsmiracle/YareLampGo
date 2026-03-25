---
name: lampgo-vision
description: |
  Camera vision for lampgo desk lamp robot.
  Use when: user wants to see what's on the desk, detect presence, take a photo,
  analyze surroundings, or have lampgo react to visual input.
  Triggers: "look", "see", "camera", "photo", "snap", "watch", "detect", "vision",
  "看看", "拍照", "摄像头", "桌面", "周围", "什么东西".
metadata:
  openclaw:
    emoji: "📷"
---

# lampgo Vision

Use the camera to see, analyze, and react to the environment.

## Camera Discovery

List available cameras:

```bash
# Linux
v4l2-ctl --list-devices 2>/dev/null || ls /dev/video*

# Test camera works
python3 -c "import cv2; cap=cv2.VideoCapture(0); print('OK' if cap.isOpened() else 'FAIL'); cap.release()"
```

## Quick Commands

### Snap a photo

```bash
WORKSPACE=$(openclaw config get workspace 2>/dev/null || echo "$HOME/.openclaw/workspace")
python3 {baseDir}/scripts/snap.py "$WORKSPACE/lampgo_snap.jpg"
python3 {baseDir}/scripts/snap.py "$WORKSPACE/desk.jpg" --device 0 --width 1920 --height 1080
```

Returns JSON: `{"ok": true, "path": "...", "size": 226245, "device": 0}`

### Analyze image (encode for LLM vision)

```bash
bash {baseDir}/scripts/analyze.sh "$WORKSPACE/lampgo_snap.jpg" --resize 800x600
```

Returns JSON with `data_uri` field — pass to vision-capable LLM for analysis.

## Workflow: Snap → Analyze → React

1. **Snap**: `python3 {baseDir}/scripts/snap.py "$WORKSPACE/snap.jpg"`
2. **Read**: Use the `read` tool to view the image
3. **Analyze**: Determine what's in the scene
4. **React**: Send appropriate lampgo command

### Reaction Table

| Scene | lampgo Reaction |
|-------|----------------|
| Person at desk | `lampgo invoke play_recording name=wake_up` + LED=smiley |
| Empty desk | `lampgo invoke play_recording name=doze_off` + LED=sleep |
| Messy desk | `lampgo invoke play_recording name=confused` + LED=question |
| Someone waving | `lampgo invoke play_recording name=happy_wiggle` + LED=smiley |
| Dark room | `lampgo invoke set_expression mode=white` (light up) |
| Food on desk | `lampgo invoke play_recording name=curious` + LED=thinking |

## Dependencies

- `opencv-python` (for camera capture)
- `ffmpeg` (optional, for image resize)

Install: `uv add opencv-python` (in the lampgo project)
