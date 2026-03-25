---
name: lampgo
description: |
  Control lampgo desk lamp robot with 5-DOF mechanical arm and 8x8 NeoPixel LED expression matrix.
  Use when: user wants to control the desk lamp, trigger robot actions/emotions/expressions,
  set LED patterns, adjust brightness, play animations, record new actions, or interact physically.
  Triggers: "lamp", "lampgo", "台灯", "机械臂", "LED", "表情", "动作", "灯光",
  "dance", "nod", "wave", "look", "跳舞", "点头", "摇头", "害羞", "开心".
metadata:
  openclaw:
    emoji: "🔦"
---

# lampgo — Intelligent Desk Lamp Robot

Control a 5-DOF desk lamp robot: 37 pre-recorded actions + 30 LED expressions + smooth joint control + teach recording.

All commands use `lampgo invoke` via IPC (daemon must be running). Response time: <100ms.

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

### Verify Setup

```bash
lampgo status
lampgo skills
```

### Start Daemon (if not already running)

```bash
nohup lampgo run > /tmp/lampgo_daemon.log 2>&1 &
```

## Quick Commands

```bash
# Play an action
lampgo invoke play_recording name=nod
lampgo invoke play_recording name=dance
lampgo invoke play_recording name=happy_wiggle

# Set LED expression
lampgo invoke set_expression mode=smiley
lampgo invoke set_expression mode=heart

# Move joints directly
lampgo invoke move_to base_yaw=30 base_pitch=-20

# Parametric motion
lampgo invoke nod count=3
lampgo invoke headshake count=2
lampgo invoke dance cycles=4
lampgo invoke idle_sway duration=10

# Return to safe position
lampgo invoke return_safe

# Emergency stop
lampgo invoke estop

# Query status
lampgo status
```

## Semantic Mapping (Emotion → Action + LED)

Use these combos to express emotions. Format: `action + LED mode + brightness`.

| Emotion | Action | LED | Brightness | Command |
|---------|--------|-----|------------|---------|
| 打招呼/醒来 | wake_up | smiley (10) | 255 | `lampgo invoke play_recording name=wake_up` then `lampgo invoke set_expression mode=smiley` |
| 开心/感谢 | happy_wiggle | smiley (10) | 255 | action=happy_wiggle, LED=smiley |
| 思考 | deep_think | thinking (26) | 150 | action=deep_think, LED=thinking |
| 工作照明 | working | white (4) | 180 | action=working, LED=white |
| 同意/点头 | nod | check (14) | 200 | `lampgo invoke nod count=2` then LED=check |
| 不同意/摇头 | headshake | cross (15) | 150 | `lampgo invoke headshake count=2` then LED=cross |
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
```bash
# First set LED expression
lampgo invoke set_expression mode=smiley
# Then play action
lampgo invoke play_recording name=happy_wiggle
# After action completes, return to safe position
lampgo invoke return_safe
```

## Direct Joint Control

For precise positioning beyond preset actions:

```bash
# Move specific joints
lampgo invoke move_to base_yaw=30 base_pitch=-20

# Move with custom velocity
lampgo invoke move_to base_yaw=50 velocity=60

# Read current status
lampgo status
```

See `references/joints.md` for full joint names, ranges, and direction descriptions.

## Recording New Actions (录制新动作)

Users can create custom actions by physically moving the arm:

```bash
# 1. Start recording (torque disengages, user moves arm freely)
lampgo record my_new_action --fps 30

# 2. User moves the arm to create the desired motion
# 3. Press Ctrl+C to stop recording

# 4. Play back to verify
lampgo invoke play_recording name=my_new_action

# 5. If satisfied, suggest an LED pairing
```

### Recording workflow for OpenClaw:
1. Ask the user what action they want to create
2. Run `lampgo record <name>` in the terminal
3. Tell the user to move the arm freely, then press Ctrl+C when done
4. Play it back for verification
5. Suggest an LED expression that matches the action's emotion

## Safety Rules

**ALWAYS follow these rules when controlling the lamp:**

1. **Always return to safe position**: After every action completes, run `lampgo invoke return_safe`. This is the #1 rule.
2. **No sudden large movements**: The motion runtime uses trapezoidal velocity profiles for smoothness. Trust it.
3. **Respect joint limits**: Values are auto-clamped by the safety kernel. Don't intentionally exceed limits.
4. **Emergency stop**: If the user says "stop" or "停", immediately run `lampgo invoke estop`.
5. **No unauthorized movement**: Never trigger motor actions during idle unless the user explicitly requests it.
6. **Workspace awareness**: The lamp has a physical reach radius. Warn the user before large movements.
7. **One action at a time**: Wait for the current action to complete before starting the next.

## Text Intent Routing

Send free text through the fast path (keyword matching + optional LLM):

```bash
lampgo text "做个害羞的表情"
lampgo text "点头三次"
lampgo text "你好"
```

The daemon's IntentRouter will match keywords and invoke the appropriate skill automatically.

## References

- **Full action list**: See `references/actions.md` — 37 actions with recommended LED pairings
- **Full LED list**: See `references/led-modes.md` — 30 expression modes
- **Joint reference**: See `references/joints.md` — 5 joints with ranges, directions, templates

## Environment Variables

Set in `~/.openclaw/.env` (auto-configured by setup):

```
LAMPGO_MOTOR_PORT=/dev/ttyUSB0
LAMPGO_LED_PORT=/dev/ttyUSB1
LAMPGO_LAMP_ID=AL01
```
