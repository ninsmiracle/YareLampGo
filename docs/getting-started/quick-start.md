# 快速上手

本文面向第一次运行 `lampgo` 的用户，目标是在几分钟内启动本地 Web 控制台，并确认软件链路或真实硬件链路可用。

## 环境要求

- Python 3.12+
- `uv`
- macOS、Linux 或 Windows
- 可选硬件：兼容的 5-DOF Feetech 机械臂台灯、ESP32 LED 控制器、摄像头、麦克风

Python 由 `uv` 自动管理，通常不需要手工创建虚拟环境。

## 安装 uv

macOS / Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

macOS 也可以使用 Homebrew:

```bash
brew install uv
```

Windows PowerShell:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 获取代码

```bash
git clone https://github.com/ninsmiracle/lampgo.git
cd lampgo
uv sync
```

如果只想先调试 Web、配置和 Agent 链路，可以不连接硬件。

## 首次配置

```bash
uv run lampgo onboard
```

向导会依次处理：

| 步骤 | 说明 |
| --- | --- |
| `env_check` | 检查 Python、`uv` 和关键依赖。 |
| `hardware` | 配置电机串口、摄像头、麦克风和 ESP32 无线设备，支持自动探测。 |
| `llm` | 配置 LLM provider、模型、Base URL 和 API key。 |
| `persona_memory` | 导入默认或自定义人设与记忆文件。 |
| `openclaw_plugin` | 检测到 OpenClaw 时提示安装 lampgo 插件和 AgentSkill。 |

配置默认写入：

```text
~/.lampgo/
├── config.toml
├── credentials.json
├── memory/
└── <persona>.md
```

`credentials.json` 保存敏感凭证，请勿提交到仓库。

## 启动 Web 控制台

连接真实硬件：

```bash
uv run lampgo run --web
```

无硬件模式：

```bash
uv run lampgo run --web --no-hw
```

指定端口：

```bash
uv run lampgo run --web --web-port 18790
```

打开 <http://127.0.0.1:8420> 后，可以使用聊天、技能、表情、录制和设置面板。

## 验证安装

另开一个终端执行：

```bash
uv run lampgo status
uv run lampgo skills
uv run lampgo text "点个头"
```

如果没有启动守护进程，`status` 和 `text` 会提示先运行 `lampgo run`。硬件未连接时，运动和 LED 技能会自动跳过真实写入，但 Web 与路由仍可调试。

## 连接真实硬件

1. 接入电机总线和 LED 控制器。
2. 执行 `uv run lampgo detect` 查看候选串口。
3. 执行 `uv run lampgo onboard` 或在 Web 设置页写入串口。
4. 首次使用新设备时执行 `uv run lampgo calibrate`。
5. 启动 `uv run lampgo run --web`。

常见调试命令：

```bash
uv run lampgo ping
uv run lampgo invoke return_safe
uv run lampgo invoke set_expression expression=heart
uv run lampgo estop
uv run lampgo clear
```

## 下一步

- 阅读 [配置说明](configuration.md) 理解配置来源和凭证管理。
- 阅读 [动作与表情](../guides/motion-and-expression.md) 学习录制、回放和 LED 控制。
- 阅读 [OpenClaw 集成](../guides/openclaw-integration.md) 将台灯接入 Agent 工作流。
