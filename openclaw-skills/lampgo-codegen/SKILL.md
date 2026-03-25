---
name: lampgo-codegen
description: |
  Generate executable Python scripts for complex lampgo animations and behaviors.
  Use when: user describes complex motion sequences, choreographed dances, multi-step behaviors,
  loops, conditional reactions, or anything too complex for single CLI commands.
  Triggers: "写一段动画", "生成脚本", "编排动作", "循环", "复杂动作", "舞蹈编排",
  "choreograph", "script", "sequence", "animate", "generate code".
  NOT for: simple one-shot commands (use lampgo skill instead).
metadata:
  openclaw:
    emoji: "🎬"
    requires:
      bins: ["python3"]
---

# lampgo Code Generation

Generate and execute Python scripts for complex lampgo animations.

## When to Use This Skill

Use this instead of the basic `lampgo` skill when:
- User wants a **sequence of movements** ("wave then bow then dance")
- User wants **loops or repetition** ("nod 3 times then spin")
- User wants **timed choreography** ("look left for 2 seconds, then right")
- User wants **smooth continuous motion** ("slowly scan left to right")
- The motion is too complex for a single `lampgo invoke` command

## Script Template

```python
#!/usr/bin/env python3
"""lampgo animation: [describe what it does]"""
import json, socket, time

SOCK = "/tmp/lampgo.sock"

def send(cmd):
    """Send a command to the lampgo daemon and return the response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(json.dumps(cmd).encode() + b"\n")
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    s.close()
    return json.loads(buf.strip())

def invoke(skill_id, **params):
    """Invoke a lampgo skill and wait for completion."""
    return send({"cmd": "invoke", "skill_id": skill_id, "params": params})

def move_to(wait_time=1.5, **joints):
    """Move to target joints and wait."""
    result = invoke("move_to", **joints)
    time.sleep(wait_time)
    return result

def led(mode):
    return invoke("set_expression", mode=mode)

def play(name):
    result = invoke("play_recording", name=name)
    time.sleep(3)  # Approximate action duration
    return result

# ========== Animation starts here ==========

# [Generated code goes here]

# ========== Return to safe position ==========
invoke("return_safe")
```

Save scripts to `/tmp/lampgo_script.py` and execute with `python3 /tmp/lampgo_script.py`.

## Code Generation Rules

### Safety
1. **Always end with `invoke("return_safe")`** — every script must return to safe position
2. **Never exceed joint limits** — safety kernel clamps values, but don't rely on it
3. **Add `time.sleep()` between movements** — minimum 0.5s for transitions
4. **Keep scripts under 100 lines** — break longer scripts into sections
5. **No infinite loops** — always use bounded loops or timeouts

### Motion Quality
1. Use `move_to(wait_time=N, ...)` for sequential positions
2. Use small increments + short sleep for smooth continuous motion:
   ```python
   for yaw in range(-80, 81, 4):
       move_to(wait_time=0.05, base_yaw=yaw)
   ```
3. Use `play(name)` for pre-recorded animations (best quality)
4. Combine LED changes with motion for expressiveness

### Examples

**"Nod 3 times then wave":**
```python
led("check")
for _ in range(3):
    move_to(wait_time=0.5, base_pitch=-30)
    move_to(wait_time=0.5, base_pitch=0)
led("smiley")
play("happy_wiggle")
invoke("return_safe")
```

**"Slowly scan left to right":**
```python
led("thinking")
for yaw in range(-80, 81, 4):
    move_to(wait_time=0.05, base_yaw=yaw)
for yaw in range(80, -81, -4):
    move_to(wait_time=0.05, base_yaw=yaw)
invoke("return_safe")
```

**"Greeting sequence":**
```python
led("smiley")
play("wake_up")
invoke("nod", count=2)
time.sleep(1)
play("happy_wiggle")
invoke("return_safe")
```

**"Dance to a beat (120 BPM)":**
```python
beat = 0.5  # 120 BPM
led("music")
for _ in range(4):
    move_to(wait_time=beat, base_yaw=-40, base_pitch=-20)
    move_to(wait_time=beat, base_yaw=40, base_pitch=-20)
invoke("return_safe")
```

## Iteration Workflow

1. Generate script based on user's description
2. Save to `/tmp/lampgo_script.py`
3. Execute: `python3 /tmp/lampgo_script.py`
4. Ask user for feedback: "速度快一点？" / "幅度大一点？"
5. Modify and re-execute
6. When satisfied, optionally save to a permanent location

## References

- See `references/api.md` for full IPC protocol, skill parameters, and joint ranges
