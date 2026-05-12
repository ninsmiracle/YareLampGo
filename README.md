# lampgo

> 桌面具身智能台灯机器人运行时 — LeLamp 5-DOF 机械臂的 Python 守护进程

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Powered by uv](https://img.shields.io/badge/powered%20by-uv-blueviolet)](https://github.com/astral-sh/uv)

`lampgo` 是运行在你电脑上的后台服务，统一调度 LeLamp 台灯的运动、表情、感知与对话能力，并通过内置 Web UI、CLI 与 HTTP 插件接口暴露给用户与上层 Agent。

---

## Features

- **安全优先的运动控制** — 梯形速度插值、关节限位、逐 tick 速度裁剪，独立 50Hz 控制线程与紧急停止通道。
- **仿生风格化动作** — 内置 `gentle` / `confident` / `curious` 等风格，支持任意动作的录制、回放与热加载。
- **LED 表情系统** — ESP32 驱动，30+ 预设表情，可编程控制。
- **多模态感知** — 摄像头抓帧、语音闭环（STT / TTS / VAD）、OpenAI 兼容的 LLM 接入。
- **唤醒词 + 实时语音对话** — `openwakeword`（hey_jarvis 等）+ LiveKit Agent SDK 实现 "唤醒 → ASR → LLM → TTS" 全闭环；前端「通话」视图可手动发起浏览器实时对话。
- **ESP32 无线感知** — XIAO ESP32S3 Sense 通过 mDNS 自动发现，作为无线摄像头 + PDM 麦克风 + I2S 扬声器（MAX98357A），支持硬件 AEC 与音量调节。
- **开箱即用的 Web UI** — 聊天、通话、录制、设置、表情面板集成在单一页面（默认 `http://127.0.0.1:8420`）。
- **OpenClaw 生态集成** — 作为 OpenClaw 的配件运行，AI Agent 可直接驱动台灯动作、LED、摄像头与记忆。
- **无硬件降级模式** — 缺少串口或设备时自动降级到 `--no-hw`，便于开发与调试。

---

## Quickstart

```bash
# 1. 安装 uv（仅一次）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 获取源码并同步依赖
git clone https://github.com/<your-org>/lampgo.git
cd lampgo
uv sync

# 3. 交互式引导（配硬件、LLM、人设）
uv run lampgo onboard

# 4. 启动守护进程 + Web UI
uv run lampgo run --web
```

打开 <http://127.0.0.1:8420> 即可使用。

---

## Installation

### 依赖

- Python 3.12+（由 `uv` 自动管理）
- [uv](https://github.com/astral-sh/uv) — 包与环境管理器
- macOS / Linux / Windows

### 安装 uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或
brew install uv

# Windows PowerShell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 克隆与同步

```bash
git clone https://github.com/<your-org>/lampgo.git
cd lampgo
uv sync
```

---

## Getting Started

### 1. 运行 `lampgo onboard`

`lampgo onboard` 提供交互式的首次配置向导，涵盖环境检查、硬件配置、LLM 接入与人设导入：

```bash
uv run lampgo onboard
```

| 步骤 | 说明 |
| --- | --- |
| `env_check` | 校验 Python、`uv` 及关键依赖。 |
| `hardware` | 配置电机串口、LED 串口、摄像头、麦克风。支持自动探测。 |
| `llm` | 选择 provider（mimo / openai / anthropic 等），写入 `~/.lampgo/credentials.json` 并校验 key。 |
| `persona_memory` | 导入自定义人设、使用默认人设或跳过。 |
| `openclaw_plugin` | 若检测到 `openclaw` CLI，自动注册 Lampgo 插件。 |

结束后会打印各步骤状态（`ok` / `skipped` / `error`）。所有配置产物写入 `~/.lampgo/`：

```
~/.lampgo/
├── config.toml          # 主配置（Web UI 同步编辑）
├── credentials.json     # LLM / 插件凭证（0600 权限，请勿提交）
├── memory/              # 长期记忆 / 人设 markdown
└── <persona>.md         # 当前激活人设
```

### 2. 启动服务

```bash
uv run lampgo run --web                 # 守护进程 + Web UI（推荐，唤醒词配置后自动启用语音）
uv run lampgo run --web --no-hw         # 无硬件模式
uv run lampgo run --web --web-port 18790
uv run lampgo run                       # 纯后台守护进程
```

### 3. 使用 Web UI

打开 <http://127.0.0.1:8420>：

- **状态胶囊** — 顶部展示摄像头、麦克风、电机状态，点击可切换设备；当 ESP32 接入扬声器时还会显示音量滑杆。
- **Settings Tab** — 调整模型、硬件端口、语音、运动与安全参数。修改硬件字段后需冷重启。
- **聊天面板** — 文字对话、触发技能、管理录制；历史会话自动保存于 `~/.lampgo/sessions.json`。
- **通话面板** — 侧边栏「通话」按钮进入实时语音对话视图，实时显示 ASR 文本、工具调用与助手回复，被打断的回复会展示「已中止」标签，结束后自动归档为会话历史。

---

## Usage

### 查看命令

```bash
uv run lampgo help          # 快速手册与调试命令
uv run lampgo --help        # 完整子命令列表
uv run lampgo <cmd> --help  # 单个子命令的参数
```

### 状态与探测

```bash
uv run lampgo status        # 守护进程状态
uv run lampgo skills        # 已注册技能
uv run lampgo detect        # 探测可用串口
uv run lampgo ping          # ping 每个舵机 ID
```

### 动作与表情

```bash
uv run lampgo move base_yaw=30 base_pitch=-20       # 直接关节控制（度）
uv run lampgo invoke return_safe                     # 回到安全位
uv run lampgo invoke dance                           # 预设动作
uv run lampgo invoke set_expression expression=heart # LED 表情
uv run lampgo text "做个害羞的表情"                   # 自然语言意图路由
```

### 录制与回放

```bash
uv run lampgo record my_action --fps 30    # 录制（Ctrl+C 结束）
uv run lampgo play my_action               # 回放
```

- Web UI 中使用「开始录制 / 结束录制」按钮（回车键快捷）；录制自动松力矩。
- 回放路径查找顺序：`assets/recordings/user/<name>.csv` → `assets/recordings/<name>.csv`；可通过 `--recordings-dir` 自定义。
- `record` 采样原始关节位置；`play` 经 `move_to` → 路径规划 → style（默认 `gentle`）→ `validate_frame` 安全校验。

### 硬件工具

```bash
uv run lampgo calibrate              # 交互式电机校准
uv run lampgo calibrate --id AL02    # 指定台灯 ID
uv run lampgo estop                  # 紧急停止
uv run lampgo clear                  # 清理僵尸进程并释放串口
```

---

## Configuration

`lampgo` 遵循单一配置源原则，所有持久化配置存于 `~/.lampgo/config.toml`。覆盖优先级（高 → 低）：

```
CLI 参数  >  ~/.lampgo/config.toml  >  内置默认值
```

- 绝大多数场景下只需通过 `lampgo onboard` 与 Web UI 修改配置，无需手工编辑文件。
- 敏感信息（LLM / 插件 token）单独存储于 `~/.lampgo/credentials.json`（权限 `0600`）。

---

## Voice & Realtime Conversation

`lampgo` 内置完整的语音闭环，由两条链路协同工作：

| 链路 | 触发方式 | 组件 |
| --- | --- | --- |
| **唤醒词链路** | 持续监听麦克风，命中关键词（默认 `hey_jarvis`）后激活 | `openwakeword` + Lampgo LiveKit Agent SDK + LiveKit Server |
| **浏览器通话** | Web UI 侧边栏「通话」按钮手动发起 | LiveKit JS Client + Agent SDK |

两条链路都将音频经由 LiveKit Server 投递给 Agent SDK；Agent SDK 完成 ASR/TTS，并把 LLM 推理委托回 `lampgo` 暴露的 OpenAI 兼容接口 `/v1/chat/completions`。

### 必要配置

在 `~/.lampgo/config.toml` 的 `[voice]` 中设置：

```toml
[voice]
wake_word            = "hey_jarvis"               # 留空 = 禁用唤醒词
livekit_url          = "ws://192.168.31.116:7880" # LiveKit Server
livekit_api_key      = "..."
livekit_api_secret   = "..."
volcengine_app_id    = "..."
volcengine_access_token = "..."
livekit_tts_voice    = "BV700_streaming"
chat_model           = "mimo-v2-pro"
silence_timeout_s    = 60
```

可选依赖一并装好（`uv sync --extra voice` 或在 `pyproject.toml` 已声明的 optional `voice` 组中自动随 `lampgo-livekit-agent-sdk` 一起安装）。

### 行为要点

- **请求抢占** — 后端在新的用户语句到来时取消上一次未完成的 LLM 推理，避免堆积长尾。
- **流式 `say` 工具** — 助手对白逐句通过 SSE 推送到 TTS，不需要等整轮工具执行完毕。
- **硬件优雅降级** — 当 `motion`/`led` 等硬件未连接时，相关技能直接返回 `{"note": "hardware not connected, skipped"}`，LLM 不会陷入重试循环。
- **配置热重载** — Web UI 修改语音相关字段后 1 秒内自动重启 `WakeLoop` 和 Agent SDK 子进程，无需重启守护进程。

---

## ESP32 Wireless Camera / Mic / Speaker

[`ESP32_CAMERA/`](../ESP32_CAMERA) 目录是配套的 XIAO ESP32S3 Sense 固件，可作为 lampgo 的无线感知前端：

| 通道 | URI | 协议 | 用途 |
| --- | --- | --- | --- |
| 摄像头 | `http://<host>/snapshot.jpg` / `/stream` | HTTP MJPEG | LLM 视觉输入 |
| 麦克风 | `ws://<host>/ws/audio` | WS Binary, 16 kHz PCM16LE | 唤醒词与通话麦克风源（ESP-SR AFE AEC 处理后输出） |
| 扬声器 | `ws://<host>/ws/speaker` | WS Binary, 16 kHz PCM16LE | 浏览器将 TTS 输出回灌到 ESP32 + MAX98357A 播放，并同步作为 AEC 参考信号 |
| 设备 API | `http://<host>/device/status`, `/device/config` | HTTP JSON | 查询 / 设置音量、相机参数等 |

### 硬件接线（XIAO ESP32S3 Sense + MAX98357A）

| 信号 | ESP32 GPIO | XIAO 标注 | MAX98357A |
| --- | :---: | :---: | --- |
| BCLK | 1 | D0 | BCLK |
| LRCLK | 2 | D1 | LRC |
| DIN | 4 | D3 | DIN |
| VIN | 5V | 5V | VIN |
| GND | GND | GND | GND |
| 增益 | — | — | GAIN 悬空 = 9 dB（或拉到 VIN 取最低 3 dB） |
| 使能 | — | — | **SD 建议短接至 VIN**，避免悬空抖动导致底噪 |

板载 PDM 麦克风固定在 GPIO 41 (DATA) / 42 (CLK)，无需接线。

### 编译与刷写

XIAO ESP32S3 Sense 的 PSRAM 默认关闭，必须显式启用：

```bash
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi /path/to/ESP32_CAMERA
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi -p /dev/cu.usbmodem21401 /path/to/ESP32_CAMERA
arduino-cli monitor --fqbn esp32:esp32:XIAO_ESP32S3 --config baudrate=115200 -p /dev/cu.usbmodem21401
```

首次上电进入 SoftAP 配网模式（SSID 形如 `Lampgo-Setup-XXXX`），连接后浏览器打开 `http://192.168.4.1` 输入 WiFi 凭证；之后 `lampgo` 通过 mDNS 自动发现 `lampgo-cam-XXXX.local`。

### 自动接入

固件启动后 `WorkMode` 会：

1. 先连接 WiFi（失败则保留 SoftAP，便于重新配网）；
2. 初始化相机（无 PSRAM 时自动降到 QVGA 并跳过相机但保留 mic/speaker，不再 halt）；
3. 注册 `/snapshot`、`/stream`、`/ws/audio`、`/ws/speaker`、`/device/*` 等 HTTP/WS 端点；
4. 通过 mDNS 广播。

在 `[device_esp32]` 配置中 `enabled=true` 后，lampgo 优先使用 ESP32 摄像头/麦克风，运行时断连不会自动回退本地设备（避免对话中途切换），仅在冷启动时回退。

### 顶栏音量滑杆

当 ESP32 设备在线时，Web UI 顶栏会出现「ESP32 扬声器音量」滑杆，调整后通过 `POST /device/config { speaker_volume: 0..1 }` 实时下发到 ESP32，固件在写入 I2S 之前做 PCM 缩放。

---

## OpenClaw Integration

`lampgo` 可作为 [OpenClaw](https://github.com/openclaw/openclaw) 的配件，让 AI Agent 直接驱动台灯。集成分层：

| 能力 | `openclaw` CLI | `lampgo` Plugin | Skill 注册 |
| --- | :---: | :---: | :---: |
| 信息查询 / shell / web_search / 代码生成 | 必需 | — | 可选 |
| 机械臂、LED、摄像头控制 | 必需 | 必需 | 必需 |
| 保存录制动作 + 注册别名 | 必需 | 必需 | 必需 |
| 反问用户（`lampgo_ask_user`） | 必需 | 必需 | 必需 |

### 一键安装

```bash
uv run lampgo run --web                  # 先启动守护进程
uv run lampgo install-openclaw --yes     # 注册插件 + 写 token + 加载 Skill
uv run lampgo install-openclaw --check   # 仅检查状态
```

该命令会：探测 `openclaw` 二进制 → 注册 `openclaw-plugin-lampgo/` → 在 `~/.openclaw/openclaw.json` 中写入 `lampgoApiBase` 与 `lampgoPluginToken` → 将 `lampgo` 加入 `plugins.allow` → 在 `skills.load.extraDirs` 注册 AgentSkill。修改插件代码后重跑同一命令即可刷新。

### AgentSkill 能力

| 能力 | 描述 |
| --- | --- |
| 基础控制 | 37 动作 + 30 LED 表情 + 5-DOF 关节精确控制 |
| 视觉感知 | 摄像头抓帧、场景分析、自动反应 |
| 复杂动画 | 多步编排，AI 设计关键帧后热加载为新录制 |
| 视觉伺服 | 全景扫描 → 目标定位 → 伸手触碰 |
| 人设与记忆 | 读写 SOUL / AGENTS / PROFILE 与核心 / 每日记忆 |
| 人机交互 | 通过 TTS 与 Web UI 发起反问并等待用户回复 |

### Plugin Tools（HTTP）

运动与表情：

| Tool | 说明 |
| --- | --- |
| `lampgo_move` | 移动关节到目标角度（可部分关节） |
| `lampgo_play` | 播放预录动作 |
| `lampgo_expression` | 设置 LED 表情 |
| `lampgo_save_recording` | 保存新录制 CSV，支持同时注册自然语言别名，热加载 |
| `lampgo_recordings` | 列出所有可用录制 |

感知与状态：

| Tool | 说明 |
| --- | --- |
| `lampgo_status` | 查询守护进程与硬件状态快照 |
| `lampgo_sensor_context` | 聚合摄像头 / 语音 / 传感器配置上下文 |
| `lampgo_camera_snap` | 抓取当前画面（返回 base64 data URL） |

人设与记忆：

| Tool | 说明 |
| --- | --- |
| `lampgo_get_persona` | 读取 SOUL / AGENTS / PROFILE（或全部）的 Markdown |
| `lampgo_save_persona` | 覆盖指定人设文件（自动备份） |
| `lampgo_get_memory` | 读取核心记忆、今日记忆或指定日期的每日记忆 |
| `lampgo_save_memory` | 追加条目到每日记忆，可选同步写入核心 MEMORY.md |

人机交互：

| Tool | 说明 |
| --- | --- |
| `lampgo_ask_user` | 通过 TTS / Web UI 发起提问并等待回复（支持超时） |

详见 [`docs/project_description.md`](docs/project_description.md)。

---

## Development

```bash
uv sync --group dev                # 安装开发依赖
uv run ruff check lampgo/          # Lint
```

欢迎提交 Issue 与 Pull Request。提交前请确保 `uv run ruff check` 无新增告警，并在 Commit message 中描述变更动机与影响范围。

---

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。
