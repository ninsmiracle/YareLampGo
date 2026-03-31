# Joint Names & Ranges

| Joint | Range (°) | Positive (+) | Negative (-) | 作用 |
|-------|-----------|-------------|-------------|------|
| base_yaw | -150 ~ 150 | 右转 | 左转 | 底座水平旋转，决定灯"朝向哪个方向" |
| base_pitch | -100 ~ 65 | 前倾 | 后仰 | 整条臂前后倾斜，配合 elbow 调整灯头高度和远近 |
| elbow_pitch | -90 ~ 100 | 弯曲（灯头降低） | 伸展（灯头升高） | 肘关节折叠，改变灯头到底座的距离和高度 |
| wrist_roll | -75 ~ 75 | 顺时针 | 逆时针 | 灯头绕臂轴旋转（微调，日常动作几乎不用） |
| wrist_pitch | -45 ~ 100 | 灯头朝下 | 灯头朝上 | **灯头俯仰 = 摄像头拍摄角度**。+朝下照桌面，-朝上看天花板 |

## 关节分组

- **方向控制**：`base_yaw`（左右看）
- **高度/距离控制**：`base_pitch` + `elbow_pitch` 配合（整条臂的姿态）
- **摄像头角度**：`wrist_pitch`（灯头朝上/朝下，直接决定拍到什么）
- **微调**：`wrist_roll`（灯头歪头，一般不需要动）

## 参考姿态

| 姿态 | base_pitch | elbow_pitch | wrist_pitch | 说明 |
|------|-----------|-------------|-------------|------|
| 安全位 (idle) | -45 | 83 | 3 | 放松弯曲，日常待机 |
| 站直 (stand tall) | 0 | -85 | 30 | 臂完全伸展向上，灯头微朝下 |
| 看桌面 (look at desk) | -10 | 25 | 90 | 臂前伸，摄像头照桌面 |
| 前倾 (lean forward) | 65 | -70 | 50 | 身体大幅前倾，臂向后伸展平衡 |
| 后仰 (lean backward) | -98 | -11 | 100 | 身体大幅后仰，灯头朝下补偿 |

> **关键规律**（从 moveforward / movebackward 录制中学到）：
> - `base_pitch` 和 `elbow_pitch` 是运动链，需要配合。
> - 站直需要 `base_pitch≈0` + `elbow_pitch≈-85`。`elbow_pitch=-60` 肘部仍然可见弯曲。
> - 前倾时 `base_pitch` 大正值 + `elbow_pitch` 大负值（反向伸展平衡）。
> - 后仰时 `base_pitch` 大负值，`elbow_pitch` 接近 0（不需要太多伸展）。
> - `wrist_pitch` 始终在补偿——后仰时朝下(100)、站直时微朝下(30)、看桌面时大幅朝下(90)。

## 安全位 (Safe Position)

```
base_yaw=29  base_pitch=-45  elbow_pitch=83  wrist_roll=5  wrist_pitch=3
```

## Invocation

```bash
# Move specific joints
lampgo invoke move_to base_yaw=30 base_pitch=-20

# Return to safe position
lampgo invoke return_safe
```
