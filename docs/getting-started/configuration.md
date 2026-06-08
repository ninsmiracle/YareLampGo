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

## 文件位置

```text
~/.lampgo/
├── config.toml          # Web / onboard 写入的本地配置覆盖
├── credentials.json     # LLM API Key，权限应为 0600
├── memory/              # 长期记忆和每日记忆
└── <persona>.md         # 当前人设文件
```

`~/.lampgo/config.toml` 和 `~/.lampgo/credentials.json` 都是本机私有文件，不要提交到仓库。火山引擎 App ID / Access Token 目前由 Web 设置页写入本地配置文件，也应按敏感信息处理。

## Web 端配置入口

启动 Web 控制台后打开 <http://127.0.0.1:8420>，进入 `设置` 页。常用配置都建议从这里保存。

### 硬件

硬件页可以配置：

- `无线接入`：ESP32 设备自动发现或指定 `lampgo-cam-XXXX.local` / IP，调整画面尺寸、JPEG 画质和 HTTP 超时。
- `本机硬件`：电机串口 `device.motor_port`、本地摄像头 `camera.port`、本地麦克风 `voice.mic_device`。
- `高级`：设备标识 `device.lamp_id` 和角度单位 `device.use_degrees`。
- `运动 / 安全`：默认动作速度、动作风格、待机随机摆动、安全速度和安全加速度。


### 模型

模型页包含两类软件配置：

- `LLM 模型`：Provider、Base URL、API Key、主模型、快速模型、消息格式、上下文窗口、输出 token、历史轮数、温度和超时。
- `声音和唤醒`：火山引擎 TTS / ASR、Edge TTS 回退、火山 App ID / Access Token、唤醒词、通话模式和回声保护。

LLM 保存后下一条消息即可生效；火山引擎和语音相关字段保存后会重建语音链路。Web 端口这类服务监听配置保存后需要重启 `lampgo run --web`。

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

## 配置火山引擎语音

火山引擎用于语音识别和语音播报：

- `stt_provider = "volcengine"`：语音转文字，默认模型为 `bigmodel`。
- `tts_provider = "volcengine"`：文字转语音，需要 App ID / Access Token。
- 未配置火山凭证或改选 `edge-tts` 时，聊天播报会回退到 Edge TTS。

开通服务前先准备火山引擎账号。可参考 [豆包语音快速入门](https://www.volcengine.com/docs/6561/163043?lang=zh) 完成账号注册、实名认证、创建应用和服务开通；如果找不到 App ID / Access Token，可参考 [豆包语音控制台使用 FAQ](https://www.volcengine.com/docs/6561/196768?lang=zh) 查看参数位置。YareLampGo 当前需要填写的是旧版控制台兼容参数 `App ID` 和 `Access Token`。

开通时建议确认应用已启用这些能力：

- [大模型录音文件极速版识别](https://www.volcengine.com/docs/6561/1631584)：`volc.bigasr.auc_turbo`。用于 Web 录音、唤醒链路等短音频识别，接口会一次请求直接返回结果。
- [大模型流式语音识别](https://www.volcengine.com/docs/6561/1354869?lang=zh)：`volc.bigasr.sauc.duration`。用于实时通话 / LiveKit 语音链路。
- [豆包语音合成大模型 2.0](https://www.volcengine.com/docs/6561/1329505?lang=zh)：`seed-tts-2.0`。用于默认音色 `zh_female_vv_uranus_bigtts` 的台灯播报。

Web 配置步骤：

1. 打开 `设置 -> 模型 -> 声音和唤醒`。
2. 将 `播报服务` 设为 `火山引擎 TTS（App ID / Token）`。
3. 填写 `火山引擎 App ID` 和 `火山引擎 Access Token`。
4. 首次使用可保持默认音色 `zh_female_vv_uranus_bigtts`。
5. 如需调整音色或模型，展开 `高级：火山 TTS 音色和模型`。`TTS Model` 可选 `seed-tts-2.0-standard` / `seed-tts-2.0-expressive`；不确定时保持默认空值。
6. 点击 `保存`，然后在聊天或通话里测试语音播报。

对应配置大致如下：

```toml
[voice]
stt_provider = "volcengine"
stt_model = "bigmodel"
tts_provider = "volcengine"
tts_model = ""
tts_voice = "zh_female_vv_uranus_bigtts"
volcengine_app_id = ""
volcengine_access_token = ""
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
motor_port = "/dev/ttyUSB0"
lamp_id = "AL02"
use_degrees = true

[camera]
port = ""

[device_esp32]
enabled = true
preferred_host = ""
framesize = 8
jpeg_quality = 10
http_timeout_s = 5.0
```

- `motor_port`：Feetech 电机总线串口，可在 Web 硬件页保存后热重连。
- `lamp_id`：用于匹配 `assets/calibration/` 下的校准文件。
- `camera.port`：本地 USB 摄像头索引，如 `0` 或 `1`；使用 ESP32 摄像头时通常留空。
- `device_esp32.preferred_host`：留空表示自动发现，也可指定 `lampgo-cam-XXXX.local` 或设备 IP。

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

- 不要提交 `~/.lampgo/config.toml`、`~/.lampgo/credentials.json`、`.env`、API key、火山引擎 token 或插件 token。
- 公开 README 中尽量使用通用 provider 描述，例如 OpenAI-compatible、Anthropic-compatible、local。
- 如果项目依赖私有包源，请在发布前提供公开安装路径或将相关能力标记为可选。
