---
name: lampgo
description: |
  Control lampgo desk lamp robot: 5-DOF mechanical arm + 8x8 NeoPixel LED expression matrix.
  Capabilities: basic control, camera vision, complex animations, visual servoing (search & touch).
  Use when: user wants to control the desk lamp, trigger actions/emotions/expressions,
  set LED patterns, play animations, record new actions, take photos, detect objects,
  create choreographed motion sequences, or physically interact with the environment.
  Triggers: "lamp", "lampgo", "台灯", "机械臂", "LED", "表情", "动作", "灯光",
  "dance", "nod", "wave", "look", "跳舞", "点头", "摇头", "害羞", "开心",
  "look", "see", "camera", "photo", "snap", "watch", "detect", "vision",
  "看看", "拍照", "摄像头", "桌面", "周围", "什么东西",
  "写一段动画", "生成脚本", "编排动作", "循环", "复杂动作", "舞蹈编排",
  "choreograph", "script", "sequence", "animate",
  "寻找并触碰", "找可乐", "碰到", "触碰桌面上的", "search and touch", "find the object",
  "point at", "touch", "找到", "指向".
metadata:
  openclaw:
    emoji: "🔦"
    requires:
      bins: ["python3"]
---

# lampgo — Intelligent Desk Lamp Robot

Control a 5-DOF desk lamp robot with dynamic C6 eye clips, reusable S3 LED
effects, saved expression presets, smooth joint control, teach recording,
camera vision, and visual servoing.

Use the OpenClaw plugin tools (preferred, keeps all interactions inside OpenClaw):

- `lampgo_move` (joint move via `move_to`)
- `lampgo_play` (play recording via `play_recording`)
- `lampgo_expression_catalog` (live eyes, LED effects, presets, and capacity)
- `lampgo_expression` (play a discovered expression id via `set_expression`)
- `lampgo_compose_expression` (play an unsaved eye + LED combination)
- `lampgo_save_expression_preset` (save only after explicit user confirmation)
- `lampgo_camera_snap` (take a photo)
- `lampgo_ask_user` (ask the user a question with TTS)
- `lampgo_save_recording` (save a new CSV recording)
- `lampgo_status` / `lampgo_recordings` (introspection)

The lampgo daemon must be running locally.

## First-Time Setup (首次配置)

Run the setup script — it auto-detects hardware and configures everything:

```bash
python3 {baseDir}/scripts/setup.py
```

The script will:
1. Check that `lampgo` is installed
2. **Auto-detect serial ports** (motor bus + LED controller)
3. Check calibration file
4. Write detected values to `~/.openclaw/.env`
5. Start the daemon

If auto-detection succeeds, **no user input is needed**. If it fails, ask the user:

> "我需要你的硬件信息来配置台灯：
> 1. **舵机串口** (e.g. /dev/ttyUSB0) — 运行 `ls /dev/ttyUSB* /dev/ttyACM*` 查看
> 2. **LED串口** (可选，没有就留空)"

Then write to env:

```bash
python3 {baseDir}/scripts/setup.py  # Re-run after user provides info
```

## Quick Commands

- Play an action: call `lampgo_play { name: "nod" }`
- Set LED expression: call `lampgo_expression { mode: "smiley" }`
- Move joints: call `lampgo_move { joints: { base_yaw: 30, base_pitch: -20 } }`
- Query status: call `lampgo_status`
- Query recorded actions: call `lampgo_recordings`
- Take photo: call `lampgo_camera_snap`

## LED Expression Keys

Use the exact LED mode keys from `references/led-modes.md` when calling
`lampgo_expression`.

- Use keys like `smiley`, `heart`, `focused`, `wink`, `sleep`, `myu7gt`.
- When pairing an expression with a recording, set the expression first, then call `lampgo_play`.

## Semantic Mapping (Intent → Recorded Action + LED)

Recorded motion is the source of truth. Before choosing an action, use
`lampgo_recordings` or `references/actions.md` and match the user's intent to the
recording descriptions. Do not use old action names that are not in the current
recording catalog.

**Combo execution pattern:**
- `lampgo_expression { mode: "smiley" }`
- `lampgo_play { name: "excited" }`

## Direct Joint Control

For precise positioning beyond preset actions:

- Use `lampgo_move { joints: {...} }`
- Use `lampgo_status` to read current state

See `references/joints.md` for full joint names, ranges, and direction descriptions.

## Recording New Actions (录制新动作)

User-created recordings are stored in `assets/recordings/user/` (gitignored) and automatically
shadow built-ins of the same name. Built-in recordings in `assets/recordings/` are never overwritten.

### Physical teach-in (CLI)

```bash
# 1. Start recording — torque disengages, user moves arm freely
lampgo record my_new_action --fps 30

# 2. Move the arm to shape the desired motion, then press Ctrl+C

# 3. Play back to verify
lampgo invoke play_recording name=my_new_action
```

Saved to `assets/recordings/user/my_new_action.csv` by default.

### AI-generated via OpenClaw (lampgo_save_recording)

OpenClaw can design and save a recording without physical teach-in:

1. Design the motion as a sequence of keyframes
2. Build a CSV with the required format (see below)
3. Call `lampgo_save_recording { name: "my_action", csv: "...", alias: "触发词" }`

Saved to `assets/recordings/user/<name>.csv` and hot-loaded immediately.

**CSV format:**
```
timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos
0.000,0,-45,65,0,5
0.033,5,-45,65,0,8
...
```
- `timestamp`: seconds from 0, increment by `1/fps` per frame (e.g. 30fps → +0.033)
- All angle columns use `.pos` suffix
- Values in degrees; safety kernel clamps to joint limits automatically

## Safety Rules

**ALWAYS follow these rules when controlling the lamp:**

1. **Use verified motions**: prefer recorded actions and current-state-relative moves; call `return_safe` only when the runtime exposes a verified safe-return capability.
2. **No sudden large movements**: The motion runtime uses trapezoidal velocity profiles for smoothness. Trust it.
3. **Respect joint limits**: Values are auto-clamped by the safety kernel. Don't intentionally exceed limits.
4. **Emergency stop**: if the user says "stop" or "停", immediately invoke `lampgo_move` to cease motion via lampgo safety / or call estop if exposed as a tool in your environment.
5. **No unauthorized movement**: Never trigger motor actions during idle unless the user explicitly requests it.
6. **Workspace awareness**: The lamp has a physical reach radius. Warn the user before large movements.
7. **One action at a time**: Wait for the current action to complete before starting the next.

## Text Intent Routing

Send free text through the fast path (keyword matching + optional LLM):

If you are using lampgo's local chat entry, free text is routed by lampgo itself (fast path → local LLM → OpenClaw).

The daemon's IntentRouter will match keywords and invoke the appropriate skill automatically.

---

## Vision (视觉感知)

Use the camera to see, analyze, and react to the environment.

**Note:** For screen content, use OpenClaw's own screenshot tools. The lampgo camera is for the *physical world* (desk, objects, people).

### Snap a photo

Call `lampgo_camera_snap` to get a fresh snapshot (returned as a data URL).

### Workflow: Snap → Analyze → React

1. **Snap**: call `lampgo_camera_snap`
2. **Analyze**: describe what is in the image (you'll receive the data URL in the tool output)
3. **React**: call `lampgo_play` / `lampgo_expression` / `lampgo_move`

### Reaction Table

| Scene | Tool plan |
|-------|----------------|
| Person at desk | `lampgo_expression(smiley)` → `lampgo_play(wake_up)` |
| Empty desk | `lampgo_expression(sleep)` → speak softly or use a listed rest action if present |
| Messy desk | `lampgo_expression(question)` → `lampgo_play(thinking)` |
| Someone waving | `lampgo_expression(smiley)` → `lampgo_play(wake_up)` |
| Dark room | `lampgo_expression(white)` |
| Food on desk | `lampgo_expression(thinking)` → `lampgo_play(peep)` |

### Dependencies

- `opencv-python` (for camera capture)
- `ffmpeg` (optional, for image resize)

Install: `uv add opencv-python` (in the lampgo project)

---

## Complex Animations & Code Generation (复杂动画编排)

Use this when:
- User wants a **sequence of movements** ("wave then bow then dance")
- User wants **loops or repetition** ("nod 3 times then spin")
- User wants **timed choreography** ("look left for 2 seconds, then right")
- User wants **smooth continuous motion** ("slowly scan left to right")
- The motion is too complex for a single command

### Preferred approach: tool plan (not Python code)

For OpenClaw ecosystem integration, generate a **tool call sequence** rather than executable scripts:

- `lampgo_expression`
- `lampgo_play`
- `lampgo_move`
- `lampgo_status`
- `lampgo_save_recording` (to persist the sequence as a reusable action)

Only generate Python scripts when the user explicitly asks for code artifacts.

### Examples

**"Nod 3 times then wave":**
- `lampgo_expression { mode: "check" }`
- `lampgo_play { name: "nod" }` × 3
- `lampgo_expression { mode: "smiley" }`
- `lampgo_play { name: "excited" }`

**"Slowly scan left to right":**
- `lampgo_expression { mode: "thinking" }`
- Sweep `base_yaw` from -80 to 80 in bounded steps via `lampgo_move`

**"Dance to a beat (120 BPM)":**
- `lampgo_expression { mode: "music" }`
- Alternate `base_yaw` between -40 and 40 at 0.5s intervals × 4 cycles
- Settle using a verified recording if one exists; do not guess a fixed safe pose.

### Constraints

- Add delays ≥ 0.5s between sequential movements (the motion runtime handles interpolation)
- Use bounded loops only
- End with a verified settle/rest recording when available; otherwise stop after the last verified movement.
- See `references/api.md` for full IPC protocol (for Python scripts)

---

## Search & Touch (视觉伺服寻物触碰)

Find an object on the desk using the camera, center it in view, then physically reach out and touch it.

**Do not guess the object's position — use visual confirmation at each step.**

### Step 1: Enter Search Posture (进入搜索姿态)

Use a verified recording such as `look_around` when it fits the task. If a
custom search posture is needed, read `lampgo_status` first and make small,
current-state-relative adjustments instead of relying on a fixed pose constant.

### Step 2: Panoramic Scan (全景扫描)

Sweep `base_yaw` across angles: `-90`, `-45`, `0`, `45`, `90`.

At each stop:
- Move only the yaw target unless the current camera angle has already been verified.
- `lampgo_camera_snap`
- Check if the target object is visible; stop scanning once found.

### Step 3: Locate & Center (定位目标)

Once the object is in view, center it in the camera frame:

| Object position in image | Adjustment |
|--------------------------|------------|
| Left | decrease `base_yaw` |
| Right | increase `base_yaw` |
| Too low | increase `wrist_pitch` |
| Too high | decrease `wrist_pitch` |

Iterate (move → snap → analyze) until the object is reasonably centered.

### Step 4: Reach & Touch (伸头触碰)

With the object centered:
1. Keep `base_yaw` fixed
2. Use small, visually verified `base_pitch` / `elbow_pitch` adjustments.
3. Stop immediately if the object leaves view, appears too close, or the movement looks unsafe.
4. `lampgo_camera_snap` — if the object appears very large/blurry, you've reached it
5. Announce success to the user

### Step 5: Return to Safety

Settle after the interaction:
- Prefer a verified rest/settle recording if one exists in `lampgo_recordings`.
- Otherwise stop after the last verified movement; do not guess a fixed all-zero pose.

### Safety Notes for Search & Touch

- Move slowly during the reach phase — the arm is close to objects
- If the object is not reachable (too far), tell the user instead of over-extending
- The safety kernel clamps joint values if they exceed limits

---

## References

- **Recorded action list**: See `references/actions.md` and prefer live `lampgo_recordings`
- **Full LED list**: See `references/led-modes.md` — 34 expression modes
- **Joint reference**: See `references/joints.md` — 5 joints with ranges, directions, templates
- **IPC API reference**: See `references/api.md` — socket protocol for Python scripts

## Environment Variables

Set in `~/.openclaw/.env` (auto-configured by setup):

```
LAMPGO_MOTOR_PORT=/dev/ttyUSB0
LAMPGO_LED_PORT=/dev/ttyUSB1
LAMPGO_LAMP_ID=AL02
```
