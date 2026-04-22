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

### 录制与回放（最小闭环）

```bash
# 1) 手动录制（Ctrl+C 结束）
uv run lampgo record my_action --fps 30

# 2) 回放刚录制的动作
uv run lampgo play my_action
```

- 默认录制目录：`assets/recordings/user/`（用户录制，已在 `.gitignore` 中隔离）
- 回放查找顺序：先 `assets/recordings/user/<name>.csv`，再 `assets/recordings/<name>.csv`
- 如需自定义目录：可用 `--recordings-dir /path/to/dir` 传入

### Web 端录制（run 模式）

- 启动：`uv run lampgo run --web`
- 顶栏点击“开始录制”后进入 teach 模式（自动关力矩，可手动掰动关节）
- 再次点击按钮“结束录制”后，会弹出保存对话框：
  - 输入动作名并保存
  - 或选择放弃 / 重新录制
- 快捷键：当焦点不在输入框时，回车可触发“开始/结束录制”按钮；命名弹窗打开时，回车触发“保存”

### record / play 的 style 与 safety 路径说明

- `record`：只采样 `HAL.read_positions`，不经过 style 插值
- `play`：通过 `PlayRecordingSkill -> move_to` 分段回放，走路线规划与 style（默认 `gentle`，可传 `style`）
- safety：回放路径与 `move_to` 一致，走 `validate_frame`（包含关节限位与逐 tick 速度限幅）

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

## OpenClaw 生态接入

lampgo 可作为 [OpenClaw](https://github.com/openclaw/openclaw) 生态的配件运行，使 OpenClaw 的 AI Agent 直接控制台灯机械臂。

### 快速接入

lampgo 对 OpenClaw 的依赖是**增量的**——不是所有功能都需要相同的接入深度：

| 能做什么 | 需要装 openclaw CLI | 需要装 lampgo Plugin | 需要注册 Skill |
|---|---|---|---|
| 让 OpenClaw 处理纯信息/shell/web_search/写代码类任务 | ✅ | ❌ | 可选 |
| 让 OpenClaw 触发机械臂动作、LED 表情、拍照 | ✅ | ✅ | ✅ |
| 让 OpenClaw 保存新录制动作并注册触发别名 | ✅ | ✅ | ✅ |
| 让 OpenClaw 反问用户（`lampgo_ask_user`） | ✅ | ✅ | ✅ |

**按需选装**下面这些步骤：

**1. 启动 lampgo（任何集成深度都需要）**

```bash
uv run lampgo run --web   # 开启 Web UI（默认端口 8420）
# 自定义端口三选一（优先级 CLI > 环境变量 > 配置文件）：
#   uv run lampgo run --web --web-port 18790
#   LAMPGO_WEB_PORT=18790 uv run lampgo run --web
#   LAMPGO_API_BASE=http://127.0.0.1:18790 uv run lampgo run --web   # 与 OpenClaw 插件共享同一变量
```

启动后如果看到横幅里写 `openclaw binary : found on PATH` 就可以进行任务委派。

**2. （可选）注册 AgentSkill** —— 让 OpenClaw 识别"跳舞/点头/看一下"这类关键词

在 `~/.openclaw/openclaw.json` 中添加：

```jsonc
{
  "skills": {
    "load": {
      "extraDirs": ["/path/to/lampgo/openclaw-skills"]
    }
  }
}
```

**3. （推荐）一键装 Plugin** —— 让 OpenClaw 能直接驱动机械臂/灯光/摄像头，并读写台灯的人设与记忆

```bash
lampgo install-openclaw --yes
```

这条命令会：
- 探测 `openclaw` 可执行程序
- 把本仓库的 `openclaw-plugin-lampgo/` 注册进 OpenClaw
- 在 `~/.openclaw/openclaw.json` 里写入 `plugins.entries.lampgo.config.lampgoApiBase`（台灯后端地址）和 `lampgoPluginToken`（用于记忆写入的鉴权 token）
- 把 `lampgo` 加入 `plugins.allow`
- 注册 AgentSkill 到 `skills.load.extraDirs`

如果你修改了 `openclaw-plugin-lampgo/index.ts`（比如新增工具），重跑同一条命令即可刷新：

```bash
lampgo install-openclaw --yes
```

> 想手动搞？也可以：
> ```bash
> rm -rf ~/.openclaw/extensions/lampgo
> openclaw plugins install ./openclaw-plugin-lampgo
> ```
> 但这样不会自动写 `lampgoPluginToken`，`lampgo_save_memory` 工具会拒绝写入记忆。

> **为什么不装 plugin 也"能用"？**
> lampgo 调 OpenClaw 是 `subprocess.exec openclaw agent`，OpenClaw 拿到任务后用**它自己的通用工具**（shell/fs/web_search/LLM）通常就能完成信息查询、文件编辑等非物理任务。
> 但要控制真实硬件（动作、LED、摄像头），必须让 OpenClaw 能调 `lampgo_*` tool 回调 lampgo 的 HTTP API，这就需要 plugin 了。

### 能力覆盖

通过单一 AgentSkill 包 `openclaw-skills/lampgo` 提供：

| 能力 | 描述 |
|------|------|
| 基础控制 | 37 动作 + 30 LED 表情 + 5-DOF 关节精确控制 |
| 视觉感知 | 摄像头抓帧、场景分析、自动反应 |
| 复杂动画 | 多步编排，AI 设计关键帧后热加载为新录制 |
| 视觉伺服 | 全景扫描 → 目标定位 → 伸手触碰 |

### Plugin Tools

OpenClaw 通过 HTTP 调用 lampgo 守护进程，可用工具：

| Tool | 说明 |
|------|------|
| `lampgo_move` | 关节运动 |
| `lampgo_play` | 播放预录动作 |
| `lampgo_expression` | 设置 LED 表情 |
| `lampgo_camera_snap` | 摄像头拍照 |
| `lampgo_ask_user` | 通过 TTS/Web UI 询问用户并等待回复 |
| `lampgo_save_recording` | 保存新录制文件（热加载） |
| `lampgo_status` | 查询运行状态 |
| `lampgo_recordings` | 列出所有录制 |

详见 `docs/project_description.md`。

---

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
| `lampgo.bridge.openclaw` | OpenClaw 适配器 + Plugin Bridge |
| `lampgo.bridge.desktop` | PC 桌面控制 (骨架) |
| `lampgo.perception` | 感知 (人脸检测骨架, VAD stub) |
| `lampgo.web` | Web Gateway (REST + WebSocket, 端口 8420) |

## License

Apache-2.0
