# Joint Names & Ranges

| Joint | Range (degrees) | Positive (+) | Negative (-) | Description |
|-------|----------------|-------------|-------------|-------------|
| base_yaw | -150 ~ 150 | 右转 (right) | 左转 (left) | 底座水平旋转 |
| base_pitch | -100 ~ 65 | 前倾/低头 (forward/down) | 后仰/抬头 (backward/up) | 底座前后俯仰 |
| elbow_pitch | -90 ~ 100 | 肘弯曲/灯头下降 (bend/lower) | 肘伸展/灯头上升 (extend/raise) | 肘关节弯曲 |
| wrist_roll | -75 ~ 75 | 顺时针 (clockwise) | 逆时针 (counter-clockwise) | 腕部旋转 |
| wrist_pitch | -45 ~ 100 | 低头 (tilt down) | 抬头 (tilt up) | 灯头俯仰 |

## Safe Position (安全位)

```
base_yaw=29  base_pitch=-45  elbow_pitch=83  wrist_roll=5  wrist_pitch=3
```

## Look at Desk Template (看桌面姿势)

```
base_pitch=-40  elbow_pitch=50  wrist_pitch=70
```

## Invocation

```bash
# Move specific joints
lampgo invoke move_to base_yaw=30 base_pitch=-20

# Return to safe position
lampgo invoke return_safe
```
