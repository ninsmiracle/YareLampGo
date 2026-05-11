# 配置说明

`lampgo` 的运行配置以 `~/.lampgo/config.toml` 为主，推荐通过 `uv run lampgo onboard` 和 Web 设置页维护。仓库中的 `lampgo.toml.example` 只是字段参考，不会被运行时自动读取。

## 配置优先级

从高到低：

```text
CLI 参数 > 环境变量 LAMPGO_* / .env > ~/.lampgo/config.toml > 内置默认值
```

常用覆盖方式：

```bash
uv run lampgo run --web --motor-port /dev/tty.usbmodem1101
uv run lampgo run --web --led-port /dev/tty.usbserial-0001
uv run lampgo run --web --lamp-id AL02
```

## 文件位置

```text
~/.lampgo/
├── config.toml          # 主配置
├── credentials.json     # API key / token，权限应为 0600
├── memory/              # 长期记忆和每日记忆
└── <persona>.md         # 当前人设文件
```

环境变量 `LAMPGO_HOME` 可用于测试或多实例隔离：

```bash
LAMPGO_HOME=/tmp/lampgo-dev uv run lampgo run --web --no-hw
```

## 常见配置项

### 设备

```toml
[device]
motor_port = "/dev/ttyUSB0"
led_port = "/dev/ttyUSB1"
lamp_id = "AL02"
use_degrees = true
```

- `motor_port`：Feetech 电机总线串口。
- `led_port`：ESP32 LED 控制器串口，留空则禁用 LED。
- `lamp_id`：用于匹配 `assets/calibration/` 下的校准文件。
- `use_degrees`：是否使用角度制。

### 运动与安全

```toml
[motion]
tick_rate_hz = 50.0
default_max_velocity = 120.0

[safety]
max_velocity = 180.0
max_acceleration = 900.0
```

运动参数可通过 Web 设置页热更新；硬件串口、设备 ID 等字段通常需要重启服务。

### LLM

```toml
[llm]
provider = "openai"
model = "gpt-4o-mini"
fast_model = "gpt-4o-mini"
api_base = ""
temperature = 0.3
max_tokens = 4096
timeout_s = 15.0
```

API key 不建议写入 `config.toml`，请通过 onboard、Web 设置页或环境变量写入 `~/.lampgo/credentials.json`。

常见环境变量：

```bash
export LAMPGO_LLM_API_KEY="sk-..."
export LAMPGO_LLM_PROVIDER="openai"
export LAMPGO_LLM_MODEL="gpt-4o-mini"
```

### 摄像头与语音

```toml
[camera]
port = ""

[voice]
stt_provider = ""
tts_provider = ""
tts_voice = "zh-CN-XiaoxiaoNeural"
wake_word = ""
vad_enabled = false
```

- `camera.port` 可填 USB 摄像头索引，如 `0` 或 `1`。
- `stt_provider` 留空时禁用语音转文字。
- `tts_provider` 留空时禁用语音播报。
- `wake_word` 留空时不启用唤醒词。

## 查看与修改配置

推荐方式：

```bash
uv run lampgo onboard
uv run lampgo run --web
```

然后在 Web 控制台的 Settings 页修改配置。

手工查看：

```bash
cat ~/.lampgo/config.toml
```

仅检查字段参考：

```bash
sed -n '1,200p' lampgo.toml.example
```

## 开源发布注意事项

- 不要提交 `~/.lampgo/credentials.json`、`.env`、API key 或插件 token。
- 公开 README 中尽量使用通用 provider 描述，例如 OpenAI-compatible、Anthropic-compatible、local。
- 如果项目依赖私有包源，请在发布前提供公开安装路径或将相关能力标记为可选。
