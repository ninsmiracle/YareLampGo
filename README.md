# lampgo

桌面具身智能台灯机器人运行时。

安全、低延迟、OpenClaw-ready 的 Python 运行时，用于控制 LeLamp 机械臂台灯：梯形速度插值、LED 表情、可扩展技能系统。

## 快速开始

### 1. 安装

```bash
# 需要 Python >= 3.12 和 uv
uv sync
```

### 2. 配置

第一次使用需要配置你的硬件串口。三种方式任选其一：

**方式 A: 配置文件（推荐）**

```bash
cp lampgo.toml.example lampgo.toml
# 编辑 lampgo.toml，修改 motor_port 和 led_port 为你的实际串口
```

**方式 B: 环境变量文件**

```bash
cp .env.example .env
# 编辑 .env，设置 LAMPGO_MOTOR_PORT 等
```

**方式 C: 命令行参数**

```bash
uv run lampgo run --motor-port /dev/ttyUSB0
```

查看你的串口设备:

```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

### 3. 使用

```bash
# 查看帮助
uv run lampgo help

# 查看可用技能
uv run lampgo skills

# 启动服务器 (配置好后不需要额外参数)
uv run lampgo run

# 启动语音交互
uv run lampgo run --voice

# 查询运行状态
uv run lampgo status

# 移动关节
uv run lampgo move base_yaw=30 base_pitch=-20

# 回到安全位
uv run lampgo invoke return_safe

# 跳舞
uv run lampgo invoke dance

# 设置表情（LED）
uv run lampgo invoke set_expression expression=heart

# 文本指令（走 IntentRouter）
uv run lampgo text "做个害羞的表情"

# 播放预录动作
uv run lampgo play nod
uv run lampgo play dance

# 录制新动作 (断开力矩后手动操作机械臂)
uv run lampgo record my_action

# 校准电机
uv run lampgo calibrate

# 紧急停止
uv run lampgo estop

# 清理占用进程并尝试释放力矩（串口被占用时常用）
uv run lampgo clear

# 自动探测串口
uv run lampgo detect
```

### 配置优先级

CLI 参数 > 环境变量 (`LAMPGO_*`) > `.env` 文件 > `lampgo.toml` > 内置默认值

敏感信息 (API Key) 放 `.env`，设备参数放 `lampgo.toml`。

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试 (不需要硬件)
uv run pytest

# lint
uv run ruff check lampgo/ tests/
```

## 架构

详见 `docs/architecture.md`。功能状态和验证方法详见 `docs/project_description.md`。

### 关键模块

| 模块 | 职责 |
|------|------|
| `lampgo.core.hal` | 硬件抽象层 (Feetech motors via lerobot) |
| `lampgo.core.safety` | 安全内核 (关节限位、速度裁剪、e-stop) |
| `lampgo.core.motion` | 梯形速度运动控制 (独立控制线程 50Hz) |
| `lampgo.core.config` | 配置管理 (Pydantic + TOML + .env) |
| `lampgo.core.led` | ESP32 LED 控制 |
| `lampgo.skills` | 技能系统 (基类、注册表、执行器、FSM) |
| `lampgo.bridge.openclaw` | OpenClaw 适配器 (骨架) |
| `lampgo.bridge.desktop` | PC 桌面控制 (骨架) |
| `lampgo.perception` | 感知 (人脸检测骨架, VAD stub) |

## License

Apache-2.0
