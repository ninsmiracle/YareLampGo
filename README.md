# lampgo

> 把机械臂台灯变成普通人也能玩起来的桌面小伙伴：能听你说话，能看见环境，能自己动起来，还会用动作和表情回应你。

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Powered by uv](https://img.shields.io/badge/powered%20by-uv-blueviolet)](https://github.com/astral-sh/uv)

`lampgo` 的目标很简单：降低机械臂和具身智能的使用门槛。过去这种 5 自由度机械臂更像实验室设备，普通人很难上手；`lampgo` 把电机、灯光、摄像头、麦克风和大模型接成一个本地软件系统，让开发者、创作者和普通玩家可以用网页、命令行、自然语言或 Agent 快速做出有趣的桌面互动。

项目默认提供本地 Web 控制台、CLI、HTTP / WebSocket 接口和 OpenClaw 插件，也支持无硬件模式。你可以先把软件玩法跑通，再接真实设备。

## 用户价值

<<<<<<< HEAD
- **安全优先的运动控制** — 梯形速度插值、关节限位、逐 tick 速度裁剪，独立 50Hz 控制线程与紧急停止通道。
- **仿生风格化动作** — 内置 `gentle` / `confident` / `curious` 等风格，支持任意动作的录制、回放与热加载。
- **LED 表情系统** — ESP32 驱动，30+ 预设表情，可编程控制。
- **多模态感知** — 摄像头抓帧、语音闭环（STT / TTS / VAD）、OpenAI 兼容的 LLM 接入。
- **唤醒词 + 实时语音对话** — `openwakeword`（hey_jarvis 等）+ LiveKit Agent SDK 实现 "唤醒 → ASR → LLM → TTS" 全闭环；前端「通话」视图可手动发起浏览器实时对话。
- **ESP32 无线感知** — XIAO ESP32S3 Sense 通过 mDNS 自动发现，作为无线摄像头 + PDM 麦克风 + I2S 扬声器（MAX98357A），支持硬件 AEC 与音量调节。
- **开箱即用的 Web UI** — 聊天、通话、录制、设置、表情面板集成在单一页面（默认 `http://127.0.0.1:8420`）。
- **OpenClaw 生态集成** — 作为 OpenClaw 的配件运行，AI Agent 可直接驱动台灯动作、LED、摄像头与记忆。
- **无硬件降级模式** — 缺少串口或设备时自动降级到 `--no-hw`，便于开发与调试。
=======
- **把门槛降下来**：不用从电机控制、串口协议和运动安全开始造轮子，装好就能通过 Web UI、CLI 或自然语言控制台灯。
- **让普通软件开发者也能做硬件玩法**：把动作、表情、摄像头、语音封装成技能和 API，像调用软件工具一样调用真实机械臂。
- **让创作者更容易拍出有意思的内容**：支持录制、回放、表情、舞蹈和自定义动作，适合做桌面陪伴、短视频、直播互动和 Demo。
- **让 AI 不只会聊天**：用户一句话可以变成点头、摇头、看向、灯光表情、语音回复和多步 Agent 行动。
>>>>>>> 94cea811f7e644a2c917b7ad90b62bd75d1ea660

## 适合谁

- **普通软件开发者**：想做一点真实硬件互动，但不想从底层电机和安全控制学起。
- **自媒体和内容创作者**：想让桌面设备会动、会回应、会表演，做出更有记忆点的视频或直播互动。
- **AI 硬件原型团队**：想快速验证桌面机械臂、智能台灯和具身 AI 的新场景。
- **Agent 应用团队**：想让 Agent 不只操作网页和文件，也能调用真实电机、灯光、摄像头和语音。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 安全运动控制 | 50Hz 独立控制线程、关节限位、速度/加速度裁剪、急停、串口健康检查。 |
| 仿生动作表达 | 内置点头、摇头、注视、舞蹈、闲置摆动等动作，支持 CSV 录制和回放。 |
| LED 表情系统 | 通过 ESP32 驱动 30+ 预设表情，可与动作、语音和任务状态联动。 |
| 多模态感知 | 支持摄像头抓帧、语音输入、VAD、TTS 和 OpenAI-compatible LLM 接入。 |
| 触摸反馈（开发中） | 计划在台灯头部加入触摸电容，把摸头、轻拍等反馈经控制芯片传回 PC，让台灯能感知人的接触。 |
| Web 控制台 | 默认 `http://127.0.0.1:8420`，集成聊天、录制、表情、技能和设置。 |
| OpenClaw 集成 | 将台灯能力注册为 Agent 工具，支持复杂任务规划、视觉分析和用户确认。 |
| 无硬件开发 | 缺少串口或设备时可使用 `--no-hw` 跑通 Web、配置、Agent 和路由逻辑。 |

## 快速开始

### 1. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

macOS 也可以使用：

```bash
brew install uv
```

### 2. 获取源码并安装依赖

```bash
git clone https://github.com/ninsmiracle/lampgo.git
cd lampgo
uv sync
```

### 3. 完成首次配置

```bash
uv run lampgo onboard
```

引导流程会检查环境、配置硬件串口、写入模型凭证、导入人设，并在检测到 OpenClaw 时提示安装插件。配置文件默认写入 `~/.lampgo/`，敏感凭证保存在 `~/.lampgo/credentials.json`。

### 4. 启动 Web 控制台

```bash
uv run lampgo run --web
```

打开 <http://127.0.0.1:8420>，即可使用聊天、动作、录制、表情和设置面板。

没有硬件时可以先启动纯软件模式：

```bash
uv run lampgo run --web --no-hw
```

## 常用命令

```bash
uv run lampgo help                         # 查看常用调试命令
uv run lampgo status                       # 查询守护进程状态
uv run lampgo detect                       # 自动探测串口
uv run lampgo skills                       # 列出可用技能

uv run lampgo text "做个害羞的表情"          # 自然语言路由
uv run lampgo invoke dance                 # 调用内置技能
uv run lampgo move base_yaw=30             # 直接移动指定关节
uv run lampgo play happy_wiggle            # 回放录制动作
uv run lampgo record my_action --fps 30    # 手动录制新动作

uv run lampgo calibrate                    # 交互式电机校准
uv run lampgo estop                        # 紧急停止
uv run lampgo clear                        # 清理进程并释放串口
```

更多步骤见 [快速上手](docs/getting-started/quick-start.md)。

## 系统结构

```text
User Input
CLI / Web UI / Voice / IPC / Camera / Touch feedback
        |
        v
IntentRouter
Keyword match -> LLM agent loop -> OpenClaw escalation
        |
        v
SkillExecutor
        |
        v
MotionRuntime  -- 50Hz control thread
        |
        v
SafetyKernel   -- limits / velocity / acceleration / e-stop
        |
        v
HAL            -- Feetech motor bus + ESP32 LED
```

项目采用单进程运行时：CLI、Web UI、IPC、OpenClaw 插件共享同一套技能注册表、运动运行时和安全内核。简单指令通过关键词快速命中，复杂任务进入 LLM 工具调用，再复杂的多步任务可以升级到 OpenClaw。

## 组件路径

| 路径 | 说明 |
| --- | --- |
| `lampgo/core/` | HAL、运动控制、安全内核、配置、事件总线、LED 控制。 |
| `lampgo/skills/` | 技能框架、内置动作、录制回放、表情和组合技能。 |
| `lampgo/perception/` | 意图路由、LLM 工具调用、摄像头、存在检测。 |
| `lampgo/device/` | ESP32 摄像头/麦克风发现、音频流和外部设备桥接；触摸反馈后续也会走设备层接入。 |
| `lampgo/voice/` | 麦克风、VAD、STT、TTS、唤醒词和语音循环。 |
| `lampgo/web/` | Starlette Web Gateway、REST API、WebSocket 和静态控制台。 |
| `lampgo/bridge/` | OpenClaw、桌面控制和外部生态桥接。 |
| `assets/` | 设备校准文件和内置 CSV 动作资产。 |
| `openclaw-plugin-lampgo/` | OpenClaw 插件，将 lampgo 能力暴露为 Agent 工具。 |
| `openclaw-skills/` | OpenClaw AgentSkill 说明和动作、关节、LED 参考资料。 |

## 文档

<<<<<<< HEAD
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
=======
| 分类 | 文档 |
>>>>>>> 94cea811f7e644a2c917b7ad90b62bd75d1ea660
| --- | --- |
| 入门 | [文档中心](docs/README_zh.md)、[快速上手](docs/getting-started/quick-start.md)、[配置说明](docs/getting-started/configuration.md) |
| 使用指南 | [动作与表情](docs/guides/motion-and-expression.md)、[OpenClaw 集成](docs/guides/openclaw-integration.md) |
| 架构 | [系统架构](docs/architecture.md)、[项目说明](docs/project_description.md) |
| 开发 | [贡献指南](docs/development/contributing.md)、[示例代码](examples/) |

## OpenClaw 集成

`lampgo` 可以作为 OpenClaw 的硬件配件运行，让 Agent 读取台灯状态、控制关节、播放动作、切换 LED 表情、抓取摄像头画面、写入记忆或向用户发起确认。

```bash
uv run lampgo run --web
uv run lampgo install-openclaw --yes
```

集成细节见 [OpenClaw 集成指南](docs/guides/openclaw-integration.md)。

## 开发

```bash
uv sync --group dev
uv run ruff check lampgo tests
uv run pytest
```

提交 PR 前请确保文档、示例和相关测试同步更新。硬件相关改动建议同时说明测试设备、串口、校准文件和是否覆盖 `--no-hw` 模式。

## 开源状态

项目仍处于早期阶段，API、配置字段和硬件适配可能继续变化。开源前建议检查：

- 不提交 `~/.lampgo/credentials.json`、`.env`、私有 token 或内部服务地址。
- 将内部说明、商业素材和未授权图片留在非公开分支或私有文档中。
- 确认硬件校准文件、录制动作和第三方依赖的授权边界。

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。
