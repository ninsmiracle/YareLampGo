# 配置说明

YareLampGo 的本地配置推荐通过 `uv run lampgo onboard` 和 Web 控制台维护。仓库中的 `lampgo.toml.example` 只是字段参考，不会被运行时自动读取。

## 配置优先级

运行时真实优先级从高到低：

```text
CLI 参数 > Shell 环境变量 > 项目 .env > ~/.lampgo/credentials.json > ~/.lampgo/config.toml > 内置默认值
```

Web 控制台本身不是一个独立优先级。它会把普通配置写入 `~/.lampgo/config.toml`，把 LLM API Key 写入 `~/.lampgo/credentials.json`，并在保存后尽量热更新当前进程。若同一字段被 CLI 参数或环境变量覆盖，Web 设置页会显示覆盖提示，保存到本地文件后也不会立刻压过更高优先级。

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

`~/.lampgo/config.toml` 和 `~/.lampgo/credentials.json` 都是本机私有文件，不要提交到仓库。MiMo API Key 由 LLM 设置页写入本地凭据文件，也应按敏感信息处理。

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

- `LLM 模型`：Provider、Base URL、API Key、主模型、快速模型、消息格式、上下文窗口、输出 token、历史轮数、温度和超时。
- `声音和唤醒`：MiMo TTS / ASR（复用 LLM Base URL / API Key）、唤醒词、通话模式和回声保护。

LLM 保存后下一条消息即可生效；MiMo 语音和语音相关字段保存后会重建语音链路。Web 端口这类服务监听配置保存后需要重启 `lampgo run --web`。

## 配置 LLM

推荐从 Web 控制台配置：

1. 打开 `设置 -> 模型 -> LLM 模型`。
2. 选择 `Provider`。内置选项包括 `MiMo`、`OpenRouter`、`Anthropic`、`OpenAI`、`DeepSeek`、`Google`、`Ollama` 和 `自定义`。
3. 如果选择 `MiMo`，先参考 [Xiaomi MiMo API Open Platform](https://platform.xiaomimimo.com/docs/zh-CN/welcome) 注册并获取 API Key，也可以在官方文档里查看模型、限速和 OpenAI / Anthropic 兼容接口说明。
4. 检查 `Base URL`。内置 Provider 会自动填入默认地址；自定义代理、Azure 网关或私有网关需要手动填写。
5. 填写 `API Key`。密钥会保存到 `~/.lampgo/credentials.json`，不会写入 `config.toml`。
6. 填写 `主模型`。第一次使用建议先保持 Provider 默认模型。
7. 点击 `测试连接`。成功后点击 `保存并生效`。

高级项通常保持默认即可：

```toml
[llm]
provider = "mimo"
message_type = "openai"
api_base = "https://api.xiaomimimo.com/v1"
model = "mimo-v2.5"
fast_model = "mimo-v2.5"
enable_thinking = false
context_window = 200000
max_tokens = 20000
summary_max_tokens = 20000
history_turns = 30
temperature = 0.3
timeout_s = 300.0
```

也可以用环境变量临时覆盖：

```bash
export LAMPGO_LLM_API_KEY="api-key-placeholder"
export LAMPGO_LLM_PROVIDER="mimo"
export LAMPGO_LLM_MODEL="mimo-v2.5"
export LAMPGO_LLM_API_BASE="https://api.xiaomimimo.com/v1"
```

## 配置 MiMo 语音

MiMo 同时用于语音识别和语音播报，并复用 LLM 的 `api_base` 与 API Key：

- `stt_provider = "mimo"`，默认模型 `mimo-v2.5-asr`。
- `tts_provider = "mimo"`，默认模型 `mimo-v2.5-tts`、音色 `mimo_default`。
- 本地网页播报、设备侧唤醒语音循环与 LiveKit 通话 Agent 使用同一份 MiMo 语音设置。

配置步骤：

1. 在 Web UI 的 `设置 → 大模型` 选择 MiMo 并保存 Base URL、API Key。
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
