# 配置说明

YareLampGo 的本地配置推荐通过 `uv run lampgo onboard` 和 Web 控制台维护。仓库中的 `lampgo.toml.example` 只是字段参考，不会被运行时自动读取。

## 配置优先级

运行时真实优先级从高到低：

```text
CLI 参数 > Shell 环境变量 > ~/.lampgo/credentials.json > 项目 .env > ~/.lampgo/config.toml > 内置默认值
```

Web 控制台本身不是一个独立优先级。它会把普通配置写入 `~/.lampgo/config.toml`，把 LLM API Key 写入 `~/.lampgo/credentials.json`，并在保存后尽量热更新当前进程。凭据文件高于项目 `.env`，因此网页保存的密钥不会被仓库中的旧 `.env` 覆盖；CLI 参数和显式 Shell 环境变量仍然优先。

常用 CLI 覆盖方式：

```bash
uv run lampgo run --web --motor-port /dev/tty.usbmodem1101
uv run lampgo run --web --lamp-id AL02
uv run lampgo run --web --web-port 18790
```

环境变量 `LAMPGO_HOME` 可用于测试或多实例隔离：

```bash
LAMPGO_HOME=/tmp/lampgo-dev uv run lampgo run --web --no-hw
```

## 先选择硬件路线：旧 S3/C6 或新 P4

两条路线同时受支持，P4 不是对旧设备的覆盖升级：

| 路线 | 后端选择 | 小屏与表情资产 | 舵机通道 |
| --- | --- | --- | --- |
| 旧 S3 + C6 显示屏 | `device.motor_transport = "serial"`（默认） | 后端 → S3 → C6 | 电脑 USB → Feetech 总线 |
| P4 头部板 + C6 Wi-Fi | `device.motor_transport = "p4"` | 后端 → P4 LittleFS，P4 同步 LED/LCD | 后端 Wi-Fi → P4 → 灯头舵机 |

后端仅在你显式设置 `motor_transport = "p4"` 时才连接 P4 运动 WebSocket；发现 ESP32 设备本身不会改变旧用户的串口路径。设备的 `platform` 能力标识会让表情上传自动使用 P4 直连或旧 S3→C6 协议。完整选择、烧录和回退规则见 [P4 无线头部板路线](../hardware/p4-wireless-head.md)。

## 文件位置

```text
~/.lampgo/
├── config.toml          # Web / onboard 写入的本地配置覆盖
├── credentials.json     # LLM API Key，权限应为 0600
├── memory/              # 长期记忆和每日记忆
└── <persona>.md         # 当前人设文件
```

`~/.lampgo/config.toml` 和 `~/.lampgo/credentials.json` 都是本机私有文件，不要提交到仓库。DeepSeek、MiMo 与 MiMo 联网搜索的 API Key 都由 LLM 设置页写入本地凭据文件，也应按敏感信息处理。

## Web 端配置入口

启动 Web 控制台后打开 <http://127.0.0.1:8420>，进入 `设置` 页。常用配置都建议从这里保存。

### 硬件

硬件页可以配置：

- `无线接入`：ESP32 设备自动发现或指定 `lampgo-cam-XXXX.local` / IP，调整画面尺寸、JPEG 画质和 HTTP 超时。
- `本机硬件`：选择 `device.motor_transport`（旧 USB 串口或 P4 无线）、电机串口 `device.motor_port`、本地摄像头 `camera.port`、本地麦克风 `voice.mic_device`。
- `高级`：设备标识 `device.lamp_id`、角度单位 `device.use_degrees` 和堵转保护 `device.max_torque_pct`。
- `运动 / 安全`：默认动作速度、动作风格、待机随机摆动、安全速度和安全加速度。


### 模型

模型页包含两类软件配置：

- `LLM 模型`：Provider、Base URL、API Key、主模型、快速模型、消息格式、上下文窗口、输出 token、历史轮数、温度、超时，以及 DeepSeek Flash 到 MiMo 的自动降级。
- `声音和唤醒`：MiMo TTS / ASR。主模型为 MiMo 时复用主 MiMo 路由；主模型为 DeepSeek 等其他 Provider 时复用已配置的 MiMo 备用路由，唤醒词、通话模式和回声保护。

LLM 保存后下一条消息即可生效；MiMo 语音和语音相关字段保存后会重建语音链路。Web 端口这类服务监听配置保存后需要重启 `lampgo run --web`。

## 配置 LLM

推荐从 Web 控制台配置：

1. 打开 `设置 -> 模型 -> LLM 模型`。
2. 选择 `Provider`。内置选项包括 `MiMo`、`OpenRouter`、`Anthropic`、`OpenAI`、`DeepSeek`、`Google`、`Ollama` 和 `自定义`。
3. 日常对话推荐选择 `DeepSeek`，保持 `deepseek-flash`。如果 DeepSeek 在设定时间内没有首个有效响应，LampGo 会将这一轮请求自动改发给 MiMo；已收到任何正文、推理片段或工具调用后不会中途切换，避免重复执行动作。
4. 填写 DeepSeek API Key；在“DeepSeek Flash → MiMo 自动降级”中启用回退，并填写或保留已有的 MiMo API Key。两个密钥分别保存，MiMo 密钥不会发送到 DeepSeek。
5. 如果选择 `MiMo` 作为主模型，先参考 [Xiaomi MiMo API Open Platform](https://platform.xiaomimimo.com/docs/zh-CN/welcome) 注册并获取 API Key，也可以在官方文档里查看模型、限速和 OpenAI / Anthropic 兼容接口说明。
6. 检查 `Base URL`。内置 Provider 会自动填入默认地址；自定义代理、Azure 网关或私有网关需要手动填写。
7. 填写 `主模型`。第一次使用建议先保持 Provider 默认模型。
8. 点击 `测试连接`。成功后点击 `保存并生效`。

高级项通常保持默认即可：

```toml
[llm]
provider = "deepseek"
message_type = "openai"
api_base = "https://api.deepseek.com"
model = "deepseek-flash"
fast_model = "deepseek-flash"
enable_thinking = false
context_window = 200000
max_tokens = 20000
summary_max_tokens = 20000
history_turns = 30
temperature = 0.3
timeout_s = 300.0
fallback_enabled = true
fallback_after_s = 6.0
fallback_provider = "mimo"
fallback_model = "mimo-v2.5"
fallback_api_base = "https://api.xiaomimimo.com/v1"
```

也可以用环境变量临时覆盖：

```bash
export LAMPGO_DEEPSEEK_API_KEY="deepseek-api-key-placeholder"
export LAMPGO_MIMO_API_KEY="mimo-api-key-placeholder"
export LAMPGO_LLM_PROVIDER="deepseek"
export LAMPGO_LLM_MODEL="deepseek-flash"
export LAMPGO_LLM_API_BASE="https://api.deepseek.com"
export LAMPGO_LLM_FALLBACK_ENABLED=true
export LAMPGO_LLM_FALLBACK_AFTER_S=6
```

## 配置 MiMo 语音

MiMo 同时用于语音识别和语音播报。主模型为 MiMo 时，它复用主 LLM 的 `api_base` 与 API Key；主模型为 DeepSeek Flash 时，它复用“自动降级”中的 MiMo `api_base` 与 API Key：

- `stt_provider = "mimo"`，默认模型 `mimo-v2.5-asr`。
- `tts_provider = "mimo"`，默认模型 `mimo-v2.5-tts`、音色 `mimo_default`。
- 本地网页播报、设备侧唤醒语音循环与 LiveKit 通话 Agent 使用同一份 MiMo 语音设置。

配置步骤：

1. 在 Web UI 的 `设置 → 大模型` 配置 MiMo 主模型，或在 DeepSeek Flash → MiMo 自动降级中配置 MiMo API Key。
2. 在 `设置 → 声音和唤醒` 保持 `MiMo V2.5 TTS（复用 LLM 配置）`。
3. 保存任一 LLM 或声音设置后，后台会停止旧的语音 Agent；下一次通话会以新配置启动。

对应配置大致如下：

```toml
[voice]
stt_provider = "mimo"
stt_model = "mimo-v2.5-asr"
tts_provider = "mimo"
tts_model = "mimo-v2.5-tts"
tts_voice = "mimo_default"
wake_word = ""
call_mode = "stable"
echo_gate_hangover_ms = 1000
echo_text_filter_enabled = true
silence_timeout_s = 60
```

唤醒词目前只支持 `Hi,小星`。保存唤醒词后，Web 会尝试把 WakeNet 模型同步到 ESP32；若固件未烧录对应模型，前端会提示错误。

### 对话中的表情与动作

语音通话会把现有组合表情的名称、描述，以及录制动作目录提供给模型。模型在回复时选择
一个适合语境的已有组合表情，或明确选择不切换；后端通过正常技能执行链路循环播放，
并记录实际执行结果。优先使用屏幕眼睛与 LED 嘴巴联动的组合，不会为聊天自动创建新素材。
动画保持循环，直到新的表情或显示模式替换；明确要求“只播放一遍”时仍支持单次播放。

普通回复默认同时配合动态组合表情和小幅摆动。`say.motion` 可选 `idle_sway`（轻柔随机摆动）、
`playful_sway`（类似逗猫互动的俏皮小摆动）、`auto`（两种交替）或 `none`。后端通过
`conversation_gesture` 技能执行伴随动作，每次 3–8 秒、单关节偏移不超过 3°，从当前姿态
平滑开始并回到起始目标；不会开启摄像头跟踪或录制。明确表达特定动作时仍使用
`play_recording`，沿用录制绑定表情，不叠加默认摆动。用户可以直接说“只聊天，不要动作和表情”；
新通话取得令牌后会关闭此前的时钟、电子海洋自动刷新，让组合表情接管显示。
通话中主动重新开启的显示模式、照明和前台任务仍受保护，急停或恢复状态下跳过伴随动作。
模型选择和真实设备执行是两个环节；工具日志成功后仍需实机观察动作与表情效果。

语音中已完成的回复可通过 `say.response_complete=true` 直接结束本轮，省去只生成结束语的
额外模型请求，默认伴随摆动也可在同一轮完成；特定动作、工具报错和中途说明仍保留后续处理。MiMo 请求使用官方
`thinking.type` 控制深度思考。日志 `llm_client.stream_latency` 记录首个有效增量、完整回复
耗时与思考内容字符数，便于区分模型等待和 ASR/TTS 耗时；这不等于设备扬声器的端到端延迟。

通话每个用户轮次最多执行一次联网搜索，搜索请求最多等待 20 秒（主配置超时更短时取更短值）。
搜索结果要求包含来源和日期；超时或信息不完整时说明无法核实，不换关键词连续搜索，也不补造
天气等实时数据。`llm_client.voice_search_latency` 单独记录这一步的耗时。此预算不含主模型和语音合成时间。

`voice.echo_text_filter_enabled` 同时控制后端入口和 LiveKit 工作进程的文字回声过滤。
工作进程在 TTS 开始输出音频时登记原文，在 ASR 结果进入 LiveKit 之前进行比对；后端入口
再次保护正在执行的请求。过滤会按播报长度、排队和识别延迟保留参考文字，最长 90 秒、
最多 24 段，新通话清空。纯回声丢弃，带新指令的回声前后缀尽量剥离，否定、提问和停止
意图保留。`voice.mimo_livekit_echo_filtered` 和 `llm_compat.echo_text_*` 可用于检查结果。
这是文字过滤，不是声学回声消除；与播报内容高度相同的真实复述仍可能被误判，需要实机验证。

## 常见配置字段

### 设备

```toml
[device]
motor_transport = "p4"
motor_port = "/dev/ttyUSB0"
p4_motion_port = 82
p4_connect_timeout_s = 8.0
p4_feedback_timeout_s = 1.0
lamp_id = "AL02"
use_degrees = true
max_torque_pct = 80

[camera]
port = ""

[device_esp32]
enabled = true
preferred_host = ""
framesize = 8
jpeg_quality = 10
http_timeout_s = 5.0
```

- `motor_transport`：`serial` 保留旧 USB 舵机总线路径；`p4` 通过已配对 P4 的 WebSocket 控制头部舵机，不再要求电脑接舵机线。
- `motor_port`：仅 `serial` 使用的 Feetech 电机总线串口，可在 Web 硬件页保存后热重连。
- `p4_motion_port`：P4 运动通道端口，固件默认 `82`；设备地址复用 `device_esp32` 的发现/首选地址。
- `p4_feedback_timeout_s`：舵机遥测超时门限；超时后后端把运动链路降级，不积压旧轨迹。
- `lamp_id`：用于匹配 `assets/calibration/` 下的校准文件。
- `max_torque_pct`：电机 Torque_Limit 百分比，默认 `80`；降低堵转电流和转接板发热，不改变正常空载速度。
- `camera.port`：本地 USB 摄像头索引，如 `0` 或 `1`；使用 ESP32 摄像头时通常留空。
- `device_esp32.preferred_host`：留空表示自动发现，也可指定 `lampgo-p4-XXXX.local`、旧 `lampgo-cam-XXXX.local` 或设备 IP。P4 电机模式要求 `device_esp32.enabled = true`。

P4 无线配置示例：

```toml
[device]
motor_transport = "p4"
lamp_id = "AL02"
p4_motion_port = 82

[device_esp32]
enabled = true
preferred_host = "lampgo-p4-ABCD.local"
mic_enabled = true

[voice]
call_mode = "esp32_aec"
```

后端继续使用原有角度制动作与校准文件；原始舵机位置映射、总线串口、限位复核和掉线释放由 P4 执行。切换到 P4 前必须保证该 `lamp_id` 的五个关节校准完整，并先完成无负载/低扭矩物理验收。

### 运动与安全

```toml
[motion]
tick_rate_hz = 50.0
default_max_velocity = 120.0
default_style = "gentle"
default_playback_mode = "cleaned"
idle_sway_enabled = true
idle_sway_idle_after_s = 600.0
idle_sway_interval_s = 30.0
idle_sway_interval_jitter_s = 8.0
idle_sway_duration_s = 8.0
idle_sway_amplitude = 6.0
idle_sway_period_s = 4.5

[safety]
max_velocity = 120.0
max_acceleration = 900.0
```

`idle_sway_*` 控制待机随机摆动：台灯空闲到 `idle_sway_idle_after_s` 后，会按 `idle_sway_interval_s ± idle_sway_interval_jitter_s` 的随机间隔触发一次出厂技能 `idle_sway`。

## 查看与修改配置

推荐方式：

```bash
uv run lampgo onboard
uv run lampgo run --web
```

然后在 Web 控制台的 `设置` 页修改配置。

手工查看：

```bash
cat ~/.lampgo/config.toml
```

仅检查字段参考：

```bash
sed -n '1,200p' lampgo.toml.example
```

## 开源发布注意事项

- 不要提交 `~/.lampgo/config.toml`、`~/.lampgo/credentials.json`、`.env`、API key 或插件 token。
- 公开 README 中尽量使用通用 provider 描述，例如 OpenAI-compatible、Anthropic-compatible、local。
- 如果项目依赖私有包源，请在发布前提供公开安装路径或将相关能力标记为可选。
