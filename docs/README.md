# YareLampGo 文档中心

简体中文 | [English](README.en.md)

这里整理面向开源用户的中文文档。YareLampGo 想做的事很直接：把机械臂台灯从“实验室硬件”变成普通人也能玩起来的桌面小伙伴，让软件开发者、创作者和玩家都能更容易做出会听、会看、会动、会表达的桌面互动。

对外项目名称使用 **YareLampGo**；文档中的 `lampgo` 是内部简称、CLI 命令、包名或配置目录。

根目录 `README.md` / `README.en.md` 负责快速说明项目价值和启动方式；更长的安装、配置、动作、Codex 和开发说明放在 `docs/` 下维护。

<a id="getting-started"></a>

## 入门

| 文档 | 适合场景 |
| --- | --- |
| [快速上手](getting-started/quick-start.md) | 第一次安装、运行 Web UI、验证无硬件模式或连接真实台灯。 |
| [配置说明](getting-started/configuration.md) | 理解 `~/.lampgo/config.toml`、环境变量、凭证和常见配置项。 |

<a id="guides"></a>

## 使用指南

| 文档 | 适合场景 |
| --- | --- |
| [动作与表情](guides/motion-and-expression.md) | 调用内置动作、录制回放 CSV、控制 LED 表情、制作组合技能。 |
| [Codex 集成](guides/codex-integration.md) | 零配置连接本机 Codex，让复杂任务与台灯工具互通。 |
| [硬件公开资料](hardware/README.md) | 查看公开组件图、接线图、接线表和社区复刻结构件入口。 |

<a id="architecture-and-background"></a>

## 架构与背景

| 文档 | 适合场景 |
| --- | --- |
| [系统架构](architecture.md) | 理解 IntentRouter、SkillExecutor、MotionRuntime、SafetyKernel 和 HAL 分层。 |
| [项目说明](project_description.md) | 阅读更完整的项目背景、能力边界和技术路线。 |
| [未来方向](roadmap.md) | 了解可替换头部组件、算法扩展和后续社区共创方向。 |
| [组合技能](composed_skills.md) | 查看用户技能/组合技能的数据结构和执行规则。 |
| [硬件与资产开源范围](hardware-and-assets-scope.md) | 明确软件、固件、3D 可视化资产、社区打印件和供应商生产资料的许可证边界。 |
| [结构件文件](../assets/printable/README.md) | 查看 V1.0 STEP/STP 外观结构件、预览图和打印摆盘图。 |

<a id="examples-and-references"></a>

## 示例与参考

| 路径 | 内容 |
| --- | --- |
| [`examples/basic_motion.py`](../examples/basic_motion.py) | 基础运动控制示例。 |
| [`examples/custom_skill.py`](../examples/custom_skill.py) | 自定义技能示例。 |
| [`lampgo/mcp_stdio.py`](../lampgo/mcp_stdio.py) | Codex 调用台灯能力的本地 MCP 入口。 |

<a id="translation-policy"></a>

## 双语维护

- 默认 `README.md` 使用中文。
- 英文入口使用同目录的 `README.en.md`。
- 关键上手文档优先逐步补齐双语；更深的架构、硬件边界和开发记录可以先单语维护。
- 修改双语文档时，请在 PR 中说明另一语言是否已同步；如果暂未同步，明确标记为待补。
- 内部材料、商业 briefing、未公开规划、私有 token、内部服务地址和未授权素材不要从公开文档链接。
