# 贡献指南

感谢关注 YareLampGo。项目仍处于早期阶段，欢迎通过 Issue、PR、动作资产、硬件适配和文档补充参与。仓库内的 `lampgo` 仍作为命令、包名和内部简称使用。

## 开发环境

```bash
uv sync --group dev
```

必跑检查：

```bash
uv run pytest
```

仓库级 lint 检查目前仍有历史问题；如果本次改动涉及 Python 代码，请运行并在 PR 中说明结果：

```bash
uv run ruff check lampgo tests
```

只调试软件链路：

```bash
uv run lampgo run --web --no-hw
```

## 提交建议

- 一个 PR 聚焦一个主题，例如动作技能、Web UI、Codex 工具或文档。
- 涉及硬件行为时说明设备型号、串口、校准文件和测试动作。
- 修改配置字段时同步更新 `lampgo.toml.example`、README 和相关 docs。
- 新增用户可见命令时同步更新 `uv run lampgo help` 的输出测试。
- 不提交 `.env`、`credentials.json`、私有 token、内部服务地址或未授权素材。

## 代码结构

| 路径 | 说明 |
| --- | --- |
| `lampgo/core/` | 底层运动、安全、配置、事件和硬件抽象。 |
| `lampgo/skills/` | 可被 CLI、Web、本地 LLM、Codex 共享调用的技能系统。 |
| `lampgo/perception/` | 意图路由、摄像头和 LLM 工具调用。 |
| `lampgo/voice/` | 音频输入输出、STT、TTS、VAD 和唤醒词。 |
| `lampgo/web/` | 本地 Web 控制台、REST API 和 WebSocket。 |
| `lampgo/agent/` | 本机 Codex 的发现、注册和复杂任务执行。 |
| `tests/` | CLI、安装器、Web 配置等测试。 |

## 运动与硬件改动原则

- 上层能力通过技能调用，不绕过 `MotionRuntime` 和 `SafetyKernel`。
- 新动作优先使用目标驱动或轨迹驱动的现有范式。
- 对未知硬件或危险动作保持保守速度。
- 支持无硬件模式，避免没有设备的开发者无法运行 Web 和测试。

## 文档改动原则

- 根 README 保持短、清晰、适合第一次访问仓库的人阅读。
- 长教程放入 `docs/getting-started/` 或 `docs/guides/`。
- 架构细节放入 `docs/architecture.md` 或独立架构文档。
- 默认 `README.md` 使用中文，英文入口使用同目录的 `README.en.md`；修改双语文档时，请在 PR 中说明另一语言是否已同步。
- 内部材料、商业 briefing 和未公开规划不要从公开文档链接。
