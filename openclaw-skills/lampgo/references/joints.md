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

## Invocation

```bash
# Move specific joints
lampgo invoke move_to base_yaw=30 base_pitch=-20

# Invoke the runtime's safe-return capability, if enabled
lampgo invoke return_safe
```
