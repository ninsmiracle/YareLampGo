# lampgo

> 把机械臂台灯变成普通人也能玩起来的桌面小伙伴：能听你说话，能看见环境，能自己动起来，还会用动作和表情回应你。

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Powered by uv](https://img.shields.io/badge/powered%20by-uv-blueviolet)](https://github.com/astral-sh/uv)

`lampgo` 的目标很简单：降低机械臂和具身智能的使用门槛。过去这种 5 自由度机械臂更像实验室设备，普通人很难上手；`lampgo` 把电机、灯光、摄像头、麦克风和大模型接成一个本地软件系统，让开发者、创作者和普通玩家可以用网页、命令行、自然语言或 Agent 快速做出有趣的桌面互动。

项目默认提供本地 Web 控制台、CLI、HTTP / WebSocket 接口和 OpenClaw 插件，也支持无硬件模式。你可以先把软件玩法跑通，再接真实设备。

## 用户价值

- **把门槛降下来**：不用从电机控制、串口协议和运动安全开始造轮子，装好就能通过 Web UI、CLI 或自然语言控制台灯。
- **让普通软件开发者也能做硬件玩法**：把动作、表情、摄像头、语音封装成技能和 API，像调用软件工具一样调用真实机械臂。
- **让创作者更容易拍出有意思的内容**：支持录制、回放、表情、舞蹈和自定义动作，适合做桌面陪伴、短视频、直播互动和 Demo。
- **让 AI 不只会聊天**：用户一句话可以变成点头、摇头、看向、灯光表情、语音回复和多步 Agent 行动。

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

### macOS 音乐律动权限

`uv run lampgo onboard` 会自动准备音乐律动需要的系统音频组件。首次使用“音乐律动”时，macOS 会请求“屏幕录制/屏幕与系统音频录制”权限；允许后请重启 LampGo 再进入音乐律动。

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

| 分类 | 文档 |
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
- 确认硬件、外观、3D 打印和供应商生产资料的公开范围；生产 CAD 和供应商图纸不默认随软件仓库发布。

## License

本仓库的软件代码基于 [GNU General Public License v3.0 only](LICENSE) 开源。作者与归属信息见 [AUTHORS.md](AUTHORS.md)、[COPYRIGHT](COPYRIGHT) 和 [NOTICE](NOTICE)。

硬件、外观和 3D 打印资料不默认跟随主软件许可证；若发布社区可打印文件，应在对应目录单独声明许可证，默认建议使用 CERN-OHL-W-2.0。运行时 3D 模型仅用于 Web 可视化，不作为生产制造图纸发布。
