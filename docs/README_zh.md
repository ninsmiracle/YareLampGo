# YareLampGo 文档中心

这里整理面向开源用户的中文文档。YareLampGo 想做的事很直接：把机械臂台灯从“实验室硬件”变成普通人也能玩起来的桌面小伙伴，让软件开发者、创作者和玩家都能更容易做出会听、会看、会动、会表达的桌面互动。

对外项目名称使用 **YareLampGo**；文档中的 `lampgo` 是内部简称、CLI 命令、包名、配置目录或 OpenClaw 插件标识。

根目录 `README.md` 负责快速说明项目价值和启动方式；更长的安装、配置、动作、OpenClaw 和开发说明放在 `docs/` 下维护。

## 入门

| 文档 | 适合场景 |
| --- | --- |
| [快速上手](getting-started/quick-start.md) | 第一次安装、运行 Web UI、验证无硬件模式或连接真实台灯。 |
| [配置说明](getting-started/configuration.md) | 理解 `~/.lampgo/config.toml`、环境变量、凭证和常见配置项。 |

## 使用指南

| 文档 | 适合场景 |
| --- | --- |
| [动作与表情](guides/motion-and-expression.md) | 调用内置动作、录制回放 CSV、控制 LED 表情、制作组合技能。 |
| [OpenClaw 集成](guides/openclaw-integration.md) | 将 YareLampGo 接入 OpenClaw，让 Agent 调用台灯能力。 |

## 架构与背景

| 文档 | 适合场景 |
| --- | --- |
| [系统架构](architecture.md) | 理解 IntentRouter、SkillExecutor、MotionRuntime、SafetyKernel 和 HAL 分层。 |
| [项目说明](project_description.md) | 阅读更完整的项目背景、能力边界和技术路线。 |
| [组合技能](composed_skills.md) | 查看用户技能/组合技能的数据结构和执行规则。 |
| [硬件与资产开源范围](hardware-and-assets-scope.md) | 明确软件、固件、3D 可视化资产、社区打印件和供应商生产资料的许可证边界。 |

## 示例与参考

| 路径 | 内容 |
| --- | --- |
| [`examples/basic_motion.py`](../examples/basic_motion.py) | 基础运动控制示例。 |
| [`examples/custom_skill.py`](../examples/custom_skill.py) | 自定义技能示例。 |
| [`examples/openclaw_integration.py`](../examples/openclaw_integration.py) | OpenClaw 集成示例。 |
| [`openclaw-skills/lampgo/references/`](../openclaw-skills/lampgo/references/) | Agent 可读的关节、动作、LED 和 API 参考。 |

## 开源前检查

- 根 README 只链接适合公开发布的文档。
- 内部介绍、商业 briefing 和未公开规划不随开源仓库发布。
- 不提交 `.env`、`~/.lampgo/credentials.json`、私有模型 key、内部服务地址或未授权媒体素材。
- 确认 `pyproject.toml` 中的私有包源和可选依赖是否需要替换为公开可安装方案。
- 确认硬件、外观、3D 打印、运行时 3D 模型和供应商生产资料的公开边界。
