# YareLampGo 架构

YareLampGo 是一个运行在 PC 上的单进程机器人运行时。它负责把 Web UI、CLI、语音、Agent 和真实硬件接到一起，并保证所有机械臂动作都先经过技能系统和安全内核。仓库内的 `lampgo` 仍作为 CLI、Python 包名、配置目录和插件标识使用。

## 总览

```text
用户 / Agent
  |
  |-- Web UI / REST / WebSocket
  |-- CLI / IPC
  |-- 语音 / LiveKit
  |-- OpenClaw Plugin
  v
LampgoServer
  |
  |-- IntentRouter + LLMClient
  |-- SkillRegistry + SkillExecutor
  |-- WebGateway + IPCServer
  |-- EventBus + StateWriter
  v
设备能力
  |
  |-- MotionRuntime -> SafetyKernel -> HAL -> 5-DOF 电机
  |-- LEDController -> ESP32 LED 表情
  |-- CameraCapture -> ESP32 / 本地摄像头
  |-- WakeLoop / STT / TTS -> ESP32 / 本地麦克风和扬声器
  |-- Touch feedback -> 台灯头部触摸电容 -> 控制芯片 -> PC
```

核心原则：上层只能调用技能或接口，不能直接绕过 `MotionRuntime` / `SafetyKernel` 写电机。

## 主要入口

| 入口 | 代码 | 用途 |
| --- | --- | --- |
| CLI | `lampgo/cli.py` | 启动服务、调用技能、录制动作、校准、探测设备。 |
| IPC | `lampgo/ipc.py` | CLI 和本地脚本通过 Unix socket 调用运行中的守护进程。 |
| Web UI | `lampgo/web/gateway.py` + `lampgo/web/static/` | 浏览器控制台，提供聊天、设置、录制、表情、技能和设备管理。 |
| REST / WebSocket | `lampgo/web/gateway.py` | Web UI、OpenClaw 插件和外部程序的 HTTP / WS 接口。 |
| OpenClaw | `openclaw-plugin-lampgo/`、`lampgo/bridge/openclaw.py` | 把台灯能力暴露为 Agent 工具。 |
| 语音 | `lampgo/voice/` | 唤醒词、语音输入、LiveKit 会话、STT 和 TTS。 |

## 请求流

### 简单指令

```text
用户输入 "点头"
  -> IntentRouter 关键词命中
  -> SkillExecutor 调用 nod
  -> MotionRuntime 生成/播放轨迹
  -> SafetyKernel 校验每一帧
  -> HAL 写入电机
```

适合点头、摇头、回安全位、切表情、播放录制动作等高频操作。

### 复杂指令

```text
用户输入复杂需求
  -> IntentRouter 未命中关键词
  -> LLMClient 进入工具调用循环
  -> 调用技能 / 摄像头 / 联网搜索 / say / finish_response
  -> 本地能力不够时 escalate_to_openclaw
  -> OpenClaw 进行更长链路规划或请求用户确认
```

LLM 工具列表由当前 `SkillRegistry` 生成。用户在 Web UI 或 OpenClaw 中保存的新组合技能，会写入 `~/.lampgo/skills/user/` 并热加载到注册表。

### 语音输入

```text
麦克风 / ESP32 音频
  -> VAD / WakeLoop / LiveKit
  -> STT 转文字
  -> 复用文本请求流
  -> TTS / Web UI 返回结果
```

`voice.mic_device = "esp32"` 或 `device_esp32.mic_enabled = true` 时，系统优先使用 ESP32 麦克风；不可用时可回退到本地麦克风。

## 运行时模块

| 模块 | 职责 |
| --- | --- |
| `LampgoServer` | 组装配置、硬件、技能、路由、IPC、Web、语音和生命周期。 |
| `EventBus` | 在技能、WebSocket、TTS、OpenClaw 问询和状态更新之间传递事件。 |
| `StateWriter` | 写出最小运行状态，方便外部进程或集成读取。 |
| `SkillRegistry` | 管理内置技能和用户组合技能。 |
| `SkillExecutor` | 负责技能执行、取消、超时和当前 busy 状态。 |
| `IntentRouter` | 先做关键词快路径，未命中时交给 LLM 或 OpenClaw。 |
| `LLMClient` | OpenAI / Anthropic 兼容的多轮工具调用，支持视觉、联网搜索和技能工具。 |

## 硬件与感知

| 能力 | 代码 | 说明 |
| --- | --- | --- |
| 电机 | `lampgo/core/hal.py` | Feetech 电机总线、校准、读写关节状态。 |
| 运动 | `lampgo/core/motion.py` | 50Hz 控制循环，支持目标驱动和轨迹驱动。 |
| 安全 | `lampgo/core/safety.py` | 关节限位、速度/加速度上限、急停状态。 |
| 动作风格 | `lampgo/core/style.py`、`spring.py`、`trajectory.py` | 平滑、弹簧、呼吸感、重叠动作等表达层。 |
| LED | `lampgo/core/led.py` | 本机串口或 ESP32 `/device/led` 串口桥的 LED 表情控制。 |
| ESP32 设备 | `lampgo/device/esp32.py` | mDNS 发现、健康检查、HTTP 代理、摄像头抓帧。 |
| ESP32 音频 | `lampgo/device/audio_stream.py` | 通过 WebSocket 接收 ESP32 PCM 音频。 |
| 摄像头 | `lampgo/perception/camera.py` | 优先 ESP32 摄像头，也支持本地 OpenCV 摄像头。 |
| 触摸反馈 | 台灯头部触摸电容经控制芯片上报 PC，未来可进入事件总线并触发技能。 |

## 数据与配置

| 数据 | 位置 |
| --- | --- |
| 主配置 | `~/.lampgo/config.toml` |
| API key / token | `~/.lampgo/credentials.json` |
| 用户组合技能 | `~/.lampgo/skills/user/*.json` |
| 内置录制动作 | `assets/recordings/*.csv` |
| 用户录制动作 | `assets/recordings/user/*.csv` 或自定义 `recordings_dir` |
| 设备校准 | `assets/calibration/*.json` |
| 人设与记忆 | `~/.lampgo/`、`~/.lampgo/memory/` |

配置优先级：

```text
CLI 参数 > 环境变量 / .env > ~/.lampgo/config.toml / credentials.json > 内置默认值
```

## 启动流程

```text
load_config
  -> LampgoServer(config)
  -> 连接 HAL / LED；失败则降级到 no_hw
  -> 注册内置技能
  -> 加载用户组合技能
  -> 启动 IPCServer
  -> 启动 ESP32 发现（可选）
  -> 构建 LLMClient（有 API key 时）
  -> 启动 WebGateway（--web）
  -> 启动 WakeLoop / LiveKit Agent SDK（配置完整时）
```

`--no-hw` 模式会跳过真实电机和 LED 连接，但 Web UI、配置、LLM 路由、技能列表、OpenClaw 集成和大部分软件链路仍可运行。

## 扩展点

| 扩展方向 | 推荐方式 |
| --- | --- |
| 新动作 | 新增 `Skill`，或保存用户组合技能。 |
| 新录制 | 使用 Web UI 或 `lampgo record` 录制 CSV。 |
| 新传感器 | 先接入设备管理层，再通过 `EventBus` / 状态接口 / LLM 工具暴露。 |
| 新 Agent 能力 | 在 OpenClaw 插件中新增工具，最终仍调用 lampgo HTTP / IPC 接口。 |
| 新 UI 能力 | 在 `WebGateway` 增加 API，在 `web/static/` 增加前端交互。 |
