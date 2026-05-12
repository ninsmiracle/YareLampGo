# OpenClaw 集成

`lampgo` 可以作为 OpenClaw 的硬件插件运行，让 Agent 通过工具调用控制台灯动作、LED 表情、摄像头、记忆和用户确认流程。

## 能力分层

| 层 | 作用 |
| --- | --- |
| `lampgo` 守护进程 | 连接硬件，提供安全运动、技能执行、Web Gateway 和本地状态。 |
| `openclaw-plugin-lampgo/` | OpenClaw 插件，把 Agent 工具请求转发到 lampgo HTTP API。 |
| `openclaw-skills/lampgo/` | AgentSkill，告诉 Agent 如何使用动作、关节、LED、记忆和问询工具。 |

## 安装

先启动 lampgo：

```bash
uv run lampgo run --web
```

再安装或修复 OpenClaw 集成：

```bash
uv run lampgo install-openclaw --yes
```

仅检查当前状态：

```bash
uv run lampgo install-openclaw --check
```

安装命令会尝试：

- 探测 `openclaw` CLI。
- 注册 `openclaw-plugin-lampgo/`。
- 写入 `lampgoApiBase` 和 `lampgoPluginToken`。
- 将 `lampgo` 加入 OpenClaw 插件 allow list。
- 注册 `openclaw-skills/lampgo/` AgentSkill。

修改插件或 Skill 后，重新执行安装命令即可刷新。

## 插件工具

### 运动与表情

| Tool | 说明 |
| --- | --- |
| `lampgo_move` | 移动关节到目标角度，支持部分关节。 |
| `lampgo_play` | 播放预录动作。 |
| `lampgo_expression` | 设置 LED 表情。 |
| `lampgo_save_recording` | 保存新录制 CSV，并可注册自然语言别名。 |
| `lampgo_recordings` | 列出可用录制动作。 |

### 感知与状态

| Tool | 说明 |
| --- | --- |
| `lampgo_status` | 查询守护进程与硬件状态快照。 |
| `lampgo_sensor_context` | 聚合摄像头、语音和传感器上下文。 |
| `lampgo_camera_snap` | 抓取当前画面，返回 base64 data URL。 |

### 人设、记忆与用户确认

| Tool | 说明 |
| --- | --- |
| `lampgo_get_persona` | 读取 SOUL / AGENTS / PROFILE 等人设文件。 |
| `lampgo_save_persona` | 覆盖指定人设文件，自动备份。 |
| `lampgo_get_memory` | 读取核心记忆、今日记忆或指定日期记忆。 |
| `lampgo_save_memory` | 追加每日记忆，可选同步写入核心记忆。 |
| `lampgo_ask_user` | 通过 TTS 和 Web UI 向用户提问并等待回复。 |

## 典型工作流

1. 用户向 OpenClaw 提出复杂需求，例如“帮我设计一个欢迎客户的台灯动作”。
2. Agent 使用 `lampgo_status` 和 `lampgo_recordings` 了解设备状态与已有动作。
3. Agent 规划动作序列，调用 `lampgo_move`、`lampgo_expression` 或 `lampgo_play` 试运行。
4. 需要用户确认时调用 `lampgo_ask_user`。
5. 用户认可后，Agent 使用 `lampgo_save_recording` 保存动作资产，并注册自然语言别名。

## 配置项

插件配置 schema 位于：

```text
openclaw-plugin-lampgo/openclaw.plugin.json
```

核心字段：

| 字段 | 说明 |
| --- | --- |
| `lampgoApiBase` | lampgo Web Gateway 地址，例如 `http://127.0.0.1:8420`。 |
| `lampgoPluginToken` | 写操作共享密钥，用于保护记忆、人设等敏感接口。 |

## 排障

### OpenClaw 找不到 lampgo

确认 lampgo 已启动：

```bash
uv run lampgo status
```

确认 Web Gateway 可访问：

```bash
curl http://127.0.0.1:8420/api/status
```

### 插件配置没有生效

重新执行：

```bash
uv run lampgo install-openclaw --yes
```

如果仍失败，查看 `~/.openclaw/openclaw.json` 中是否存在 `lampgo` 插件配置。

### Agent 能调用工具但硬件不动

- 确认不是 `--no-hw` 模式。
- 执行 `uv run lampgo detect` 检查串口。
- 执行 `uv run lampgo ping` 检查电机 ID。
- 执行 `uv run lampgo invoke return_safe` 验证基础运动。
