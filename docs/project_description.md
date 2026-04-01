# lampgo 项目功能说明 & 验证指南

> 最后更新: 2026-03-31 | 版本: 0.2.1

## 当前开发阶段

**M1（设备可控基线）+ Dual-Path Architecture 已完成，OpenClaw 生态接入进入可用状态。**

- M1: 电机控制、安全内核、技能系统、LED、配置、CLI
- Phase A: Unix Socket IPC 守护进程、串口自动检测
- Phase B: 意图路由器（关键词 + LLM 回退）、语音循环（STT/TTS/VAD）
- Phase C: 1 个合并的 OpenClaw AgentSkill 包（`openclaw-skills/lampgo`，含控制/视觉/编排/视觉伺服四大能力）
- Phase D: OpenClaw Plugin Bridge（OpenClaw → HTTP → lampgo）+ lampgo 双向问询回调（`lampgo_ask_user`）

---

## 功能状态总览

| 功能 | 状态 | 说明 |
|------|------|------|
| 电机控制 (HAL) | **可用** | 连接 Feetech 电机总线，读写关节位置 |
| 梯形速度插值 (MotionRuntime) | **可用** | 独立控制线程 50Hz，解决顿挫问题 |
| 安全内核 (SafetyKernel) | **可用** | 关节限位、速度裁剪、持久 estop、串口断连检测 |
| LED 控制 | **可用** | ESP32 串口协议，30 种表情模式 |
| 技能系统 (Skill) | **可用** | 基类、注册表、执行器、FSM |
| 内置运动技能 | **可用** | move_to, return_safe, estop, nod, headshake, look_at, idle_sway, dance |
| CSV 动作回放 | **可用** | 37 个预录动作文件 |
| LED 表情技能 | **可用** | set_expression (30 种) |
| CLI | **可用** | run, move, play, skills, invoke, text, status, detect, estop, calibrate, record, clear, help |
| 配置系统 | **可用** | lampgo.toml + .env + 环境变量 + CLI 参数优先级链 |
| 示教录制 | **可用** | record 子命令，轨迹平滑和压缩 |
| **IPC 守护进程** | **可用** | Unix Socket JSON 协议，<100ms 延迟 |
| **串口自动检测** | **可用** | 自动探测 Feetech 电机总线和 ESP32 LED |
| **意图路由器** | **可用** | 关键词匹配（零延迟）+ LLM 回退（gpt-4o-mini function calling） |
| **LLM 意图分类** | **可用** | 需配置 API key，自动从技能注册表生成 function calling schema |
| **语音循环** | **可用** | VAD 检测 → STT (Whisper API) → 意图路由 → 技能执行 → TTS 回复 |
| **STT (语音识别)** | **可用** | Whisper API 客户端，需要 API key |
| **TTS (语音合成)** | **可用** | edge-tts (本地，无需 API key) |
| **VAD (语音活动检测)** | **可用** | 能量阈值检测 |
| **OpenClaw AgentSkill 包** | **可用** | 单包 `openclaw-skills/lampgo`，含核心控制/视觉/编排/视觉伺服四大能力 |
| **OpenClaw Plugin Bridge** | **可用** | `openclaw-plugin-lampgo/` 注册 `lampgo_*` tools，通过 HTTP 调用 lampgo Web Gateway |
| **OpenClaw 双向问询回调** | **可用** | OpenClaw 通过 `lampgo_ask_user` 调用 `/api/openclaw/ask`，lampgo 阻塞等待用户回复 |
| **Web Gateway 传感器 API** | **可用** | `/api/camera/snap`、`/api/sensor/context`、`/api/recordings/aliases` |
| **OpenClaw 慢路径执行链路** | **可用** | lampgo 复杂任务可进入 OpenClaw CLI（`openclaw agent ...`）执行长链路 |
| OpenClaw 网络服务端 | **骨架** | 暂未实现直接对接 OpenClaw WS 协议（当前用 plugin + CLI 组合落地） |
| PC Bridge (桌面控制) | **骨架** | 抽象接口 + StubBackend，需安装 pyautogui |
| Teleop (遥操作) | **骨架** | 代码存在但需真实硬件验证 |
| 反应式技能 | **骨架** | face_follow 是扫描 stub，presence_react 可运行但需摄像头 |

---

## 验证方法

### 前置准备

```bash
cd /path/to/lampgo
uv sync --group dev

# 配置设备
cp lampgo.toml.example lampgo.toml
cp .env.example .env
# 编辑配置文件，或使用环境变量
```

### 验证 1: 运行测试（不需要硬件）

```bash
uv run pytest -xvs
```

预期: **63 个测试全部通过**。包括 IPC、自动检测、意图路由、VAD 等新测试。

### 验证 2: 串口自动检测（需要硬件连接）

```bash
uv run lampgo detect
```

`lampgo calibrate` 在未传 `--port` 且未配置 `LAMPGO_MOTOR_PORT` 时，会自动复用上述探测逻辑选择电机端口；仅在自动探测也失败时才报错。

预期输出:
```json
{
  "motor_port": "/dev/ttyUSB0",
  "led_port": "/dev/ttyUSB1",
  "all_ports": ["/dev/ttyUSB0", "/dev/ttyUSB1"],
  "messages": ["Found 2 serial port(s)...", "Motor bus detected..."]
}
```

### 验证 3: 启动守护进程（需要硬件）

```bash
# 方式一: 自动检测 + 配置文件
uv run lampgo run

# 方式二: 手动指定端口
uv run lampgo run --motor-port /dev/ttyUSB0 --led-port /dev/ttyUSB1

# 方式三: 启用语音循环
uv run lampgo run --voice
```

预期: 守护进程启动，IPC socket 监听 `/tmp/lampgo.sock`。

### 验证 3.1: Web Gateway（可选）

如果启用 web（或通过 `lampgo run` 启动时已开启），可验证新增接口：

```bash
curl -s http://127.0.0.1:8420/api/status | jq .
curl -s http://127.0.0.1:8420/api/camera/snap | jq .
curl -s http://127.0.0.1:8420/api/sensor/context | jq .
```

### 验证 4: IPC 命令（需要运行中的守护进程）

```bash
# 查询状态
uv run lampgo status

# 通过 IPC 调用技能
uv run lampgo invoke nod count=3
uv run lampgo invoke dance cycles=2
uv run lampgo invoke set_expression mode=heart

# 自然语言意图路由
uv run lampgo text "你好"
uv run lampgo text "做个害羞的表情"
uv run lampgo text "跳个舞"

# 急停
uv run lampgo estop

# 一键清理（停止相关进程 + 释放扭矩）
uv run lampgo clear
```

### 验证 5: 技能列表

```bash
uv run lampgo skills
```

### 验证 6: 独立命令（不需要守护进程运行，但需要硬件）

```bash
# 这些命令会先尝试 IPC，如果守护进程没运行则直接连接硬件
uv run lampgo move base_yaw=30 base_pitch=-20
uv run lampgo play nod
```

### 验证 7: OpenClaw AgentSkill 注册

所有能力（控制、视觉、动画编排、视觉伺服）已合并为单一技能包 `openclaw-skills/lampgo`，无需分别安装。

只需在 `~/.openclaw/openclaw.json` 中将包含该目录的路径加入 `skills.load.extraDirs`（**一次性操作**）：

```jsonc
// ~/.openclaw/openclaw.json
{
  "skills": {
    "load": {
      "extraDirs": ["/path/to/lampgo/openclaw-skills"]
    }
  }
}
```

验证技能已被识别：

```bash
openclaw skills list | grep lampgo
# 预期输出: lampgo  🔦  Control lampgo desk lamp robot ...
```

首次配置硬件（自动检测串口，写入 `~/.openclaw/.env`）：

```bash
python3 openclaw-skills/lampgo/scripts/setup.py
```

### 验证 7b: Web UI（本地聊天界面 + OpenClaw 回调端点）

启动带 Web 的守护进程：

```bash
uv run lampgo run --web
# 或在 lampgo.toml 中加 [web] / web_enabled = true，然后 uv run lampgo run
```

浏览器打开 `http://localhost:8420`，可以：

- 文字聊天（等同 `lampgo text "..."` 命令）
- 查看实时事件流（WebSocket `/ws`）
- 查看 OpenClaw 任务进度推送

可用 REST 端点一览：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 设备状态快照 |
| `/api/invoke` | POST | 调用技能（`skill_id` + `params`） |
| `/api/camera/snap` | GET | 拍照并返回 base64 data URL |
| `/api/sensor/context` | GET | 摄像头/麦克风配置摘要 |
| `/api/recordings` | GET | 列出可用录制片段 |
| `/api/recordings/save` | POST | 保存新录制（CSV + 可选别名），OpenClaw 动态创建技能的写入入口 |
| `/api/recordings/aliases` | GET/POST | 读写关键词别名（`aliases.json`） |
| `/api/openclaw/ask` | POST | OpenClaw 向用户提问（阻塞等待回复） |
| `/api/openclaw/ask/reply` | POST | 用户在 Web UI 回复待处理的问询 |
| `/api/openclaw/callback` | POST | OpenClaw 推送任务状态到 lampgo |

### 验证 7c: OpenClaw Plugin Bridge 安装与配置

> 前提：lampgo 守护进程已以 `--web` 模式启动，监听 `http://127.0.0.1:8420`。

**安装插件到 OpenClaw：**

```bash
# 在 lampgo 项目根目录（注意是 plugins，不是 plugin）
openclaw plugins install ./openclaw-plugin-lampgo

# 如果之前装过旧版，需先删除缓存再重装
rm -rf ~/.openclaw/extensions/lampgo
openclaw plugins install ./openclaw-plugin-lampgo
```

安装完成后重启 OpenClaw Gateway 以加载插件。

**配置插件（可选，仅在 lampgo 不在默认地址时需要）：**

插件默认连接 `http://127.0.0.1:8420`，本机部署无需额外配置。如需修改，编辑 `~/.openclaw/openclaw.json`：

```json
{
  "plugins": {
    "lampgo": {
      "lampgoApiBase": "http://192.168.1.x:8420"
    }
  }
}
```

**信任插件（推荐）：**

首次启动若出现 "plugins.allow is empty" 提示，可在 `~/.openclaw/openclaw.json` 中显式信任插件，避免每次警告：

```json
{
  "plugins": {
    "allow": ["lampgo"]
  }
}
```

**验证插件注册的 tools：**

启动 OpenClaw 后，插件自动注册以下 8 个 tools：

| Tool | 说明 |
|------|------|
| `lampgo_move` | 关节运动（需用户审批） |
| `lampgo_expression` | LED 表情切换 |
| `lampgo_play` | 播放录制片段 |
| `lampgo_status` | 读取设备状态 |
| `lampgo_sensor_context` | 摄像头/麦克风配置 |
| `lampgo_camera_snap` | 拍照并返回图像 |
| `lampgo_recordings` | 列出可用录制 |
| `lampgo_save_recording` | 写入新录制 CSV + 注册别名（动态技能创建） |
| `lampgo_ask_user` | 通过 TTS/Web UI 向用户提问并等待回复 |

**快速验证（OpenClaw agent 对话）：**

```
你：控制台灯点头三次
OpenClaw：[调用 lampgo_play(name="nod")] ✓

你：帮我拍一张桌面的照片
OpenClaw：[调用 lampgo_camera_snap] → 返回 base64 图像
```

### 验证 8: lint 和代码质量

```bash
uv run ruff check lampgo/ tests/
```

预期: All checks passed!

---

## 验证 9: OpenClaw 复杂链路端到端验证

> 目标：从 lampgo Web UI 触发一条完整的复杂任务链路，经由 OpenClaw agent 创建一个新的录制技能，并热加载回 lampgo 可播放。

### 9.0 前置条件

```bash
# Terminal 1 — 启动 lampgo（需 --web）
uv run lampgo run --web

# Terminal 2 — 启动 OpenClaw Gateway（escalation 需要 Gateway 接收 agent 调用）
openclaw gateway

# 确认 lampgo plugin 已加载（列表底部应有 lampgo | loaded）
openclaw plugins list | grep lampgo

# 确认 lampgo skills 已注册（应显示 4 个 lampgo* ready）
openclaw skills list | grep lampgo

# 确认 lampgo web 就绪
curl -s http://localhost:8420/api/status | python3 -m json.tool
```

### 9.1 逐层冒烟测试（不依赖完整链路）

**Layer A — Plugin HTTP 工具直连（绕过 lampgo 路由）**

```bash
# 测试 lampgo_status 工具
openclaw agent --local --agent main \
  --message "调用 lampgo_status 工具，告诉我台灯当前的关节角度"

# 期望：输出关节角度 JSON（base_yaw / base_pitch / elbow_pitch 等）
```

**Layer B — lampgo_save_recording 写入测试**

```bash
# 直接通过 Plugin 保存一条最小 CSV
openclaw agent --local --agent main \
  --message "调用 lampgo_save_recording，name=smoke_test，csv='base_yaw,base_pitch,elbow_pitch,wrist_roll,wrist_pitch\n0,-20,30,0,0\n0,-20,30,0,5'，alias=冒烟测试"

# 验证文件已落盘
ls assets/recordings/ | grep smoke_test
curl -s http://localhost:8420/api/recordings | python3 -m json.tool | grep smoke_test
```

**Layer C — lampgo_ask_user 双向回调测试**

在另一个终端先挂起一个提问请求（模拟 OpenClaw 调用），再从 Web UI 或 curl 回复：

```bash
# 终端 A：发出问询（会阻塞直到收到回复或超时）
curl -s -X POST http://localhost:8420/api/openclaw/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "要继续吗？", "options": ["继续", "取消"], "timeout_s": 30}' &

# 终端 B：查询待处理的 ask_id（Web UI 会通过 WebSocket 收到，这里模拟回复）
# 先在浏览器 http://localhost:8420 的 WebSocket 控制台观察 openclaw_ask 事件
# 或直接 curl 回复（需先从浏览器 WS 事件或上面日志拿到 ask_id）
curl -s -X POST http://localhost:8420/api/openclaw/ask/reply \
  -H "Content-Type: application/json" \
  -d '{"ask_id": "<从 WS 事件获取>", "reply": "继续"}'
```

### 9.2 全链路集成测试（从 Web UI 触发）

**步骤 1：打开 Web UI**

浏览器访问 `http://localhost:8420`，确认聊天界面和 WebSocket 事件流正常。

**步骤 2：发送触发复杂链路的消息**

在聊天框输入以下内容（包含复合连接词"然后"，同时含有"编排/新动作"等创意标记，确保得分 ≥ 5 进入 OpenClaw 链路）：

```
帮我编排一个新动作然后保存，动作叫 hello_wave，台灯先缓慢低头然后抬起
```

**步骤 3：观察链路各节点**

| 节点 | 观察点 | 期望现象 |
|------|--------|---------|
| lampgo IntentRouter | 终端日志 | `server.openclaw_handoff` 日志出现 |
| OpenClaw agent 接管 | Terminal 2 日志 | agent 收到 task，开始调用 lampgo_* tools |
| lampgo_ask_user | Web UI | 聊天区出现"OpenClaw 问询"气泡，显示 agent 的问题 |
| 用户回复 | Web UI 输入框 | 在聊天框输入回复，按发送 |
| lampgo_save_recording | 文件系统 | `assets/recordings/hello_wave.csv` 创建 |
| 任务完成 | Web UI | 返回完成消息，可见 `openclaw_status: completed` 事件 |

**步骤 4：验证技能热加载**

```bash
# 确认 CSV 已写入
ls -la assets/recordings/ | grep hello_wave

# 确认别名注册（如 agent 调用了 alias）
curl -s http://localhost:8420/api/recordings/aliases | python3 -m json.tool

# 热加载播放验证（无需重启 lampgo）
curl -s -X POST http://localhost:8420/api/invoke \
  -H "Content-Type: application/json" \
  -d '{"skill_id": "play_recording", "params": {"name": "hello_wave"}}'

# 期望：{"ok": true, "result": {...}}，台灯执行动作
```

### 9.3 故障排查速查

| 症状 | 可能原因 | 解决 |
|------|---------|------|
| `openclaw binary not found` | PATH 缺少 openclaw | `which openclaw` 确认安装 |
| agent 返回 "Pass --to..." | 旧版 openclaw_client.py | 确认已含 `--agent main` 参数 |
| Web UI 不显示 openclaw_ask 事件 | WS 未订阅 | 刷新页面，检查 `/ws` 连接 |
| `plugin lampgo failed to load` | plugin 版本不对 | `rm -rf ~/.openclaw/extensions/lampgo && openclaw plugins install ./openclaw-plugin-lampgo` |
| `save_recording` 400 错误 | CSV 内容为空 | 确认 csv 字段非空字符串 |
| escalation 没走 OpenClaw | LLM 本地消化了任务 | 暂时注释 `.env` 中的 API key，或用含"代码/自动化/工作流"关键词的消息 |

---

## 双路径架构说明

### 快速路径 (Fast Path, sub-1s)

适用于简单交互：问候、基本手势、表情切换。

```
用户语音/文字 → STT → IntentRouter → 关键词匹配/gpt-4o-mini → 技能执行 → TTS 回复
```

延迟预算:
- 关键词命中: ~750ms (VAD 200ms + STT 300ms + 匹配 1ms + TTS 200ms)
- LLM 回退: ~1250ms (+ LLM 500ms)

### 复杂路径 (Complex Path, 5-30s)

适用于复杂任务：编排舞蹈、视觉伺服、跨系统控制。

```
OpenClaw App → Claude Opus → 读取 SKILL.md → 生成 lampgo invoke 命令 → IPC → 技能执行
```

### 两条路径共享

- 同一个 IPC 守护进程
- 同一套技能执行器
- 同一套安全内核

---

## OpenClaw Plugin Bridge 通信协议设计

OpenClaw 插件（`openclaw-plugin-lampgo/`）与 lampgo 守护进程之间选择 **HTTP** 通信，而非直接调用 `lampgo` CLI。核心理由如下：

### 1. 双向阻塞通信（决定性原因）

`lampgo_ask_user` 工具需要 OpenClaw 向用户发出问题，**阻塞等待**用户通过 lampgo 本地端（TTS 播报 + Web UI）回复后，才将答案返回给 OpenClaw agent。

CLI 无法优雅实现此语义：`lampgo ask "是否继续？"` 无法挂起等待用户在另一端点击确认；而 HTTP POST `/api/openclaw/ask` 可以做到长轮询阻塞，直至 lampgo 收到用户回复再返回 200。

### 2. 结构化/二进制数据传输

`lampgo_camera_snap` 返回 base64 图像数据；`lampgo_status` 返回嵌套 JSON 状态。CLI 的 stdout 解析脆弱且无类型保障，HTTP/JSON 可直接与 TypeScript 类型系统对接，schema 由 TypeBox 验证。

### 3. 事件推送（OpenClaw → lampgo 回调）

`/api/openclaw/callback` 允许 OpenClaw 在任务进度变化时主动 push 状态到 lampgo，lampgo 通过 WebSocket 广播给 Web UI 做进度显示。这是纯 CLI 模型无法实现的推送方向。

### 4. 沙箱兼容性

OpenClaw 对插件中 `exec` 类工具有额外审批流程（`exec.approval.requested` hook）。HTTP over `localhost` 绕开了 `exec` 工具的沙箱限制，减少不必要的权限摩擦，同时在 OpenClaw 未来收紧沙箱策略时也更具弹性。

### 延迟权衡

引入 HTTP 层的确带来约 **+5–15ms** 额外开销（相比直接 IPC），但复杂路径本身延迟为 5–30s，该开销在误差范围内可忽略。快速路径（关键词匹配、简单 LLM 调用）完全不经过 Plugin Bridge，不受影响。

---

## 配置系统说明

### 配置优先级（从高到低）

1. **CLI 参数**: `--motor-port /dev/ttyUSB0`
2. **环境变量**: `export LAMPGO_MOTOR_PORT=/dev/ttyUSB0`
3. **.env 文件**: 项目根目录下的 `.env`
4. **lampgo.toml**: 项目根目录下的配置文件
5. **内置默认值**

### 支持的环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `LAMPGO_MOTOR_PORT` | 电机串口 | (无，必须配置) |
| `LAMPGO_LED_PORT` | LED 串口 | 空 (禁用) |
| `LAMPGO_LAMP_ID` | 设备 ID | AL01 |
| `LAMPGO_LLM_API_KEY` | LLM API 密钥 | 空 |
| `LAMPGO_LLM_API_BASE` | LLM API 地址 | 空 (用官方默认) |
| `LAMPGO_LLM_PROVIDER` | LLM 提供商 | openai |
| `LAMPGO_LLM_MODEL` | LLM 模型 | gpt-4o-mini |
| `LAMPGO_LLM_FAST_MODEL` | 快速模型 | gpt-4o-mini |
| `LAMPGO_VOICE_STT_PROVIDER` | STT 提供商 | 空 (禁用) |
| `LAMPGO_VOICE_TTS_PROVIDER` | TTS 提供商 | 空 (禁用) |
| `LAMPGO_VOICE_TTS_VOICE` | TTS 声音 | zh-CN-XiaoxiaoNeural |
| `LAMPGO_RECORDINGS_DIR` | 录制目录 | assets/recordings |
| `LAMPGO_SOCKET` | IPC socket 路径 | /tmp/lampgo.sock |

---

## OpenClaw AgentSkill 包

所有能力已合并为单一包 `openclaw-skills/lampgo`，包结构：

```
openclaw-skills/
└── lampgo/
    ├── SKILL.md          # 主技能定义（含全部能力）
    ├── _meta.json        # 包元信息
    ├── scripts/
    │   ├── setup.py      # 首次硬件配置
    │   ├── snap.py       # 摄像头抓帧
    │   └── analyze.sh    # 图片 base64 编码辅助
    └── references/
        ├── actions.md    # 37 个预录动作列表
        ├── led-modes.md  # 30 种 LED 表情模式
        ├── joints.md     # 5 个关节规格与范围
        └── api.md        # IPC socket 协议（Python 脚本用）
```

单包内含以下能力分区：

| 能力分区 | 描述 | 典型触发 |
|---------|------|---------|
| 基础控制 | 37 动作 + 30 表情 + 5 自由度关节控制 + 录制保存 | 控制台灯、触发动作表情 |
| 视觉感知 | 摄像头抓帧 + 场景分析 + 自动反应 | 看桌面、拍照、检测人员 |
| 复杂动画 | 多步动作序列、编排，生成 CSV 录制保存 | 编排舞蹈、循环动作 |
| 视觉伺服 | 全景扫描 → 目标定位 → 伸手触碰 | 找桌上物品并触碰 |

### 用户创意扩展

通过 OpenClaw + Claude，用户可以:

1. **录制新动作**: 手动移动机械臂 → 保存 → 回放验证
2. **AI 生成动作**: 描述动作 → AI 设计关键帧 → `lampgo_save_recording` 热加载
3. **视觉互动**: 识别桌面物品 → 做出反应 → 伸手触碰
4. **复杂任务**: 如"帮我打光录开箱视频" → OpenClaw 完整执行并沉淀为新技能

### 用户录制文件的 Git 管理策略

用户通过 OpenClaw (`lampgo_save_recording`) 或 CLI (`lampgo record`) 创建的录制文件与项目内置动作分离存储：

```
assets/recordings/
├── nod.csv              ← 内置动作，git 追踪
├── dance.csv            ← 内置动作，git 追踪
├── ...（37 个内置）
├── aliases.json         ← 运行时关键词映射，gitignored
└── user/                ← gitignored（.gitkeep 仅追踪目录）
    ├── hello_wave.csv   ← 用户创建，不进仓库
    └── my_action.csv
```

规则：
- 用户录制与同名内置动作冲突时，**用户版优先**（`user/<name>.csv` > `<name>.csv`）
- `aliases.json` 为运行时生成，不追踪
- 贡献者若想将自定义动作提升为内置，需手动将 CSV 从 `user/` 移至根目录并提交 PR

---

## 项目技术栈

- Python >= 3.12
- uv (包管理 + 环境)
- Pydantic v2 (配置验证)
- structlog (结构化日志)
- pyserial (串口通信)
- lerobot (电机驱动，可选)
- httpx (HTTP 客户端，LLM API)
- edge-tts (TTS，本地)
- sounddevice (音频采集，可选)
- opencv-python (视觉，可选)
- pytest + pytest-asyncio (测试)
- ruff (lint)
