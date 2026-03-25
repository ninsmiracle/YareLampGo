---
name: lampgo-search-touch
description: |
  Visual servoing skill to search for an object on the desk and touch it with the lamp head.
  Use when: user asks lampgo to find, point at, or touch an object on the desk.
  Triggers: "寻找并触碰", "找可乐", "碰到", "触碰桌面上的", "search and touch", "find the object",
  "point at", "touch", "找到", "指向".
metadata:
  openclaw:
    emoji: "🎯"
---

# lampgo Search & Touch

Find an object on the desk using the camera, center it in view, then physically reach out and touch it.

## The 4-Step Execution Logic

Follow these 4 steps interactively. Do not guess the object's position — use visual confirmation.

### Step 1: Enter Search Posture (进入搜索姿态)

Extend the arm and point the camera down at the workspace:

```bash
lampgo invoke move_to base_yaw=0 base_pitch=-40 elbow_pitch=50 wrist_roll=0 wrist_pitch=70
```

### Step 2: Panoramic Scan (全景扫描)

Rotate the base to scan the desk. Take photos at each angle:

**Logic:**
1. Sweep `base_yaw` across angles: `-90`, `-45`, `0`, `45`, `90`
2. At each stop, snap a photo using the `lampgo-vision` skill:
   ```bash
   WORKSPACE=$(openclaw config get workspace 2>/dev/null || echo "$HOME/.openclaw/workspace")
   python3 {baseDir}/../lampgo-vision/scripts/snap.py "$WORKSPACE/scan.jpg"
   ```
3. Read the image and check if the target object is visible
4. Stop scanning once the object is found

**Example sweep:**
```bash
for YAW in -90 -45 0 45 90; do
    lampgo invoke move_to base_yaw=$YAW base_pitch=-40 elbow_pitch=50 wrist_pitch=70
    sleep 2
    python3 {baseDir}/../lampgo-vision/scripts/snap.py "$WORKSPACE/scan_${YAW}.jpg"
    # Read and analyze the image to check for the target
done
```

### Step 3: Locate & Center (定位目标)

Once the object is in view, center it in the camera frame:

**Visual Calibration Rules (First-Person Camera View):**
- Object on the **left** of image → decrease `base_yaw` (turn left)
- Object on the **right** of image → increase `base_yaw` (turn right)
- Object too **low** in image → increase `wrist_pitch` (tilt head down)
- Object too **high** in image → decrease `wrist_pitch` (tilt head up)

Iterate (move → snap → analyze) until the object is reasonably centered.

### Step 4: Reach & Touch (伸头触碰)

With the object centered, extend the arm to touch it:

1. Keep `base_yaw` fixed at the centered angle
2. Decrease `elbow_pitch` to push arm forward (e.g., `50` → `30`)
3. Make `base_pitch` more negative to lower the structure (e.g., `-40` → `-60`)
4. Snap a photo — if the object appears very large/blurry, you've reached it
5. Announce success to the user

```bash
lampgo invoke move_to elbow_pitch=30 base_pitch=-60
sleep 2
python3 {baseDir}/../lampgo-vision/scripts/snap.py "$WORKSPACE/touch.jpg"
```

### Step 5: Return to Safety

**Always return to safe position when done:**

```bash
lampgo invoke return_safe
```

## Safety Notes

- Move slowly during the reach phase — the arm is close to objects
- If the object is not reachable (too far), tell the user instead of over-extending
- The safety kernel will clamp joint values if they exceed limits
- If anything goes wrong, immediately: `lampgo invoke estop`
