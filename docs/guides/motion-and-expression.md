# 动作与表情

YareLampGo 把真实硬件控制包装成技能。上层用户、Web UI、CLI、LLM 和 OpenClaw 都通过技能调用运动、表情、录制和组合动作，避免绕过安全内核直接写电机。

## 内置动作

常用动作：

```bash
uv run lampgo invoke return_safe
uv run lampgo invoke nod
uv run lampgo invoke headshake
uv run lampgo invoke look_at yaw=20 pitch=-10
uv run lampgo invoke idle_sway
uv run lampgo invoke dance
```

自然语言入口：

```bash
uv run lampgo text "点个头"
uv run lampgo text "看向左边"
uv run lampgo text "跳个舞"
```

`IntentRouter` 会先尝试关键词匹配；未命中时再进入 LLM tool calling；本地无法稳妥完成的复杂任务可以升级到 OpenClaw。

## 直接移动关节

```bash
uv run lampgo move base_yaw=30 base_pitch=-20
uv run lampgo move elbow_pitch=-40 wrist_pitch=20 --velocity 90
```

关节命令仍会经过 `MotionRuntime` 和 `SafetyKernel`，包括关节限位、速度上限、加速度上限和急停状态检查。

## LED 表情

```bash
uv run lampgo invoke set_expression expression=heart
uv run lampgo invoke set_expression expression=smiley
uv run lampgo invoke set_expression expression=thinking
```

可用表情以代码中的 `LED_EXPRESSIONS` 和 Web UI 面板为准。Agent 参考资料位于：

```text
openclaw-skills/lampgo/references/led-modes.md
```

## 录制动作

录制时会释放力矩，用户可以手动摆动机械臂。按 `Ctrl+C` 结束录制。

```bash
uv run lampgo record my_action --fps 30
```

默认保存到用户动作目录；内置动作位于：

```text
assets/recordings/
```

回放：

```bash
uv run lampgo play my_action
uv run lampgo invoke play_recording name=my_action
```

回放查找顺序：

```text
assets/recordings/user/<name>.csv
assets/recordings/<name>.csv
```

也可以指定目录：

```bash
uv run lampgo play my_action --recordings-dir ./my-recordings
```

## 运动范式

### 目标驱动

适合只知道终点的动作，例如看向、回安全位、单关节移动。

```text
move_to(target) -> TrajectoryPlan -> SafetyKernel -> HAL
```

典型入口：

- `move`
- `move_to`
- `look_at`
- `return_safe`

### 轨迹驱动

适合已知完整帧序列的动作，例如录制 CSV、舞蹈、点头、摇头。

```text
stream_frames(frames, fps) -> playback tracking -> SafetyKernel -> HAL
```

录制动作不要拆成大量 `move_to` 循环，否则每段都会重新规划，容易产生顿挫。

## 组合技能

组合技能可以把多个基础技能串起来，形成可复用动作。示例文件：

```text
docs/examples/user_skill_welcome_home.json
docs/examples/user_skill_side_wiggle.json
```

组合技能的规则见 [组合技能说明](../composed_skills.md)。

## 安全建议

- 新设备首次运行前先 `calibrate`。
- 录制和回放前保持桌面空间干净。
- 调试未知动作时先降低速度，并保证 `estop` 可用。
- 不在上层 Agent 中暴露裸串口写入能力。
- 硬件异常、卡滞或运动方向不符合预期时立即执行 `uv run lampgo estop`。
