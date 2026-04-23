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

Control a 5-DOF desk lamp robot: 37 pre-recorded actions + 30 LED expressions + smooth joint control + teach recording + camera vision + visual servoing.

Use the OpenClaw plugin tools (preferred, keeps all interactions inside OpenClaw):

- `lampgo_move` (joint move via `move_to`)
- `lampgo_play` (play recording via `play_recording`)
- `lampgo_expression` (LED expression via `set_expression`)
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
- Take photo: call `lampgo_camera_snap`

## Semantic Mapping (Emotion → Action + LED)

Use these combos to express emotions. Format: `action + LED mode + brightness`.

| Emotion | Action | LED | Brightness | Tool plan |
|---------|--------|-----|------------|---------|
| 打招呼/醒来 | wake_up | smiley (10) | 255 | `lampgo_expression(smiley)` → `lampgo_play(wake_up)` |
| 开心/感谢 | happy_wiggle | smiley (10) | 255 | action=happy_wiggle, LED=smiley |
| 思考 | deep_think | thinking (26) | 150 | action=deep_think, LED=thinking |
| 工作照明 | working | white (4) | 180 | action=working, LED=white |
| 同意/点头 | nod | check (14) | 200 | `lampgo_expression(check)` → `lampgo_play(nod)` |
| 不同意/摇头 | headshake | cross (15) | 150 | `lampgo_expression(cross)` → `lampgo_play(headshake)` |
| 跳舞 | dance | music (16) | 255 | action=dance, LED=music |
| 难过 | sad | crying (11) | 80 | action=sad, LED=crying |
| 震惊 | shock | surprised (19) | 255 | action=shock, LED=surprised |
| 害羞 | shy | blush (17) | 100 | action=shy, LED=blush |
| 困惑 | confused | question (21) | 150 | action=confused, LED=question |
| 生气 | angry_jerk | angry (18) | 200 | action=angry_jerk, LED=angry |
| 睡觉/晚安 | doze_off | sleep (25) | 50 | action=doze_off, LED=sleep |
| 兴奋 | excited | smiley (10) | 255 | action=excited, LED=smiley |
| 心碎 | heartbreak | heartbreak (28) | 100 | action=heartbreak, LED=heartbreak |
| 好奇 | curious | thinking (26) | 200 | action=curious, LED=thinking |

**Combo execution pattern:**
- `lampgo_expression { mode: "smiley" }`
- `lampgo_play { name: "happy_wiggle" }`
- (Optional) `lampgo_play { name: "return_safe" }` if you model safety as a recording; otherwise call `lampgo_move` to a known safe pose.

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

1. **Always return to safe position**: after every action, move to safe pose (or call an explicit `return_safe` capability when exposed).
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
| Empty desk | `lampgo_expression(sleep)` → `lampgo_play(doze_off)` |
| Messy desk | `lampgo_expression(question)` → `lampgo_play(confused)` |
| Someone waving | `lampgo_expression(smiley)` → `lampgo_play(happy_wiggle)` |
| Dark room | `lampgo_expression(white)` |
| Food on desk | `lampgo_expression(thinking)` → `lampgo_play(curious)` |

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
- `lampgo_play { name: "happy_wiggle" }`

**"Slowly scan left to right":**
- `lampgo_expression { mode: "thinking" }`
- Sweep `base_yaw` from -80 to 80 in bounded steps via `lampgo_move`

**"Dance to a beat (120 BPM)":**
- `lampgo_expression { mode: "music" }`
- Alternate `base_yaw` between -40 and 40 at 0.5s intervals × 4 cycles
- Return to safe pose

### Constraints

- Add delays ≥ 0.5s between sequential movements (the motion runtime handles interpolation)
- Use bounded loops only
- Always end with a return-to-safe-position step
- See `references/api.md` for full IPC protocol (for Python scripts)

---

## Search & Touch (视觉伺服寻物触碰)

Find an object on the desk using the camera, center it in view, then physically reach out and touch it.

**Do not guess the object's position — use visual confirmation at each step.**

### Step 1: Enter Search Posture (进入搜索姿态)

Extend the arm and point the camera down at the workspace:

```
lampgo_move { joints: { base_yaw: 0, base_pitch: -40, elbow_pitch: 50, wrist_roll: 0, wrist_pitch: 70 } }
```

### Step 2: Panoramic Scan (全景扫描)

Sweep `base_yaw` across angles: `-90`, `-45`, `0`, `45`, `90`.

At each stop:
- `lampgo_move { joints: { base_yaw: yaw, base_pitch: -40, elbow_pitch: 50, wrist_pitch: 70 } }`
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
2. Decrease `elbow_pitch` (e.g. `50` → `30`) to push arm forward
3. Make `base_pitch` more negative (e.g. `-40` → `-60`) to lower the structure
4. `lampgo_camera_snap` — if the object appears very large/blurry, you've reached it
5. Announce success to the user

### Step 5: Return to Safety

Always return to safe position:
- `lampgo_move { joints: { base_yaw: 0, base_pitch: 0, elbow_pitch: 0, wrist_roll: 0, wrist_pitch: 0 } }`

### Safety Notes for Search & Touch

- Move slowly during the reach phase — the arm is close to objects
- If the object is not reachable (too far), tell the user instead of over-extending
- The safety kernel clamps joint values if they exceed limits

---

## References

- **Full action list**: See `references/actions.md` — 37 actions with recommended LED pairings
- **Full LED list**: See `references/led-modes.md` — 30 expression modes
- **Joint reference**: See `references/joints.md` — 5 joints with ranges, directions, templates
- **IPC API reference**: See `references/api.md` — socket protocol for Python scripts

## Environment Variables

Set in `~/.openclaw/.env` (auto-configured by setup):

```
LAMPGO_MOTOR_PORT=/dev/ttyUSB0
LAMPGO_LED_PORT=/dev/ttyUSB1
LAMPGO_LAMP_ID=AL02
```
