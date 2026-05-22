# lampgo IPC API Reference (for codegen scripts)

## Socket Protocol

Connect to Unix socket `/tmp/lampgo.sock`, send one JSON line, receive one JSON response.

## Commands

### invoke — Execute a skill
```json
{"cmd": "invoke", "skill_id": "move_to", "params": {"base_yaw": 30, "base_pitch": -20}}
{"cmd": "invoke", "skill_id": "play_recording", "params": {"name": "nod"}}
{"cmd": "invoke", "skill_id": "set_expression", "params": {"mode": "smiley"}}
{"cmd": "invoke", "skill_id": "nod", "params": {"count": 3, "speed": 80}}
{"cmd": "invoke", "skill_id": "return_safe", "params": {}}
{"cmd": "invoke", "skill_id": "estop", "params": {}}
```

### status — Get current state
```json
{"cmd": "status"}
// Response: {"ok": true, "result": {"running_skill": null, "joint_positions": {...}, ...}}
```

### cancel — Cancel running skill
```json
{"cmd": "cancel"}
```

## Skill Parameters

### move_to
- `base_yaw` (float): -150 ~ 150
- `base_pitch` (float): -100 ~ 65
- `elbow_pitch` (float): -90 ~ 100
- `wrist_roll` (float): -75 ~ 75
- `wrist_pitch` (float): -45 ~ 100
- `velocity` (float, optional): max degrees/sec

### play_recording
- `name` (str): recording name (without .csv)
- `fps` (int, optional): playback FPS override

### set_expression
- `mode` (str): exact LED mode key (see led-modes.md in lampgo skill), e.g. `smiley`, `heart`, `focused`, `wink`, `myu7gt`

### nod / headshake
- `count` (int): number of repetitions
- `amplitude` (float): degrees
- `speed` (float): deg/s

### dance
- `cycles` (int): dance cycles
- `speed` (float): deg/s

### idle_sway
- `amplitude` (float): degrees
- `period` (float): seconds per cycle
- `duration` (float): total seconds
