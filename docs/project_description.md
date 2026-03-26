# lampgo 项目功能说明 & 验证指南

> 最后更新: 2026-03-25 | 版本: 0.2.0

## 当前开发阶段

**M1（设备可控基线）+ Dual-Path Architecture 已完成。**

- M1: 电机控制、安全内核、技能系统、LED、配置、CLI
- Phase A: Unix Socket IPC 守护进程、串口自动检测
- Phase B: 意图路由器（关键词 + LLM 回退）、语音循环（STT/TTS/VAD）
- Phase C: 4 个 OpenClaw 技能包

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
| **OpenClaw 核心控制技能** | **可用** | SKILL.md + 自动配置脚本 + 37 动作 + 30 表情参考文档 |
| **OpenClaw 代码生成技能** | **可用** | 编排复杂动画脚本，IPC 协议参考文档 |
| **OpenClaw 视觉技能** | **可用** | 摄像头拍照 + 图像分析（OpenCV snap + base64 编码） |
| **OpenClaw 搜索触碰技能** | **可用** | 视觉伺服 4 步逻辑（搜索 → 扫描 → 定位 → 触碰） |
| OpenClaw 网络服务端 | **骨架** | Python API 存在，无 HTTP/gRPC server |
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

### 验证 7: OpenClaw 技能安装

```bash
# 查看 4 个技能包
ls openclaw-skills/

# 通过 OpenClaw 安装
openclaw skill install ./openclaw-skills/lampgo
openclaw skill install ./openclaw-skills/lampgo-codegen
openclaw skill install ./openclaw-skills/lampgo-vision
openclaw skill install ./openclaw-skills/lampgo-search-touch

# 首次配置 (自动检测硬件)
python3 openclaw-skills/lampgo/scripts/setup.py
```

### 验证 8: lint 和代码质量

```bash
uv run ruff check lampgo/ tests/
```

预期: All checks passed!

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

## OpenClaw 技能包

| 包名 | 功能 | 触发场景 |
|------|------|---------|
| `lampgo` | 核心控制（37 动作 + 30 表情 + 关节控制 + 录制） | 控制台灯、触发动作表情 |
| `lampgo-codegen` | 代码生成（编排复杂动画脚本） | 用户描述复杂动作序列 |
| `lampgo-vision` | 摄像头视觉（拍照分析场景） | 看桌面、检测人员 |
| `lampgo-search-touch` | 视觉伺服（搜索并触碰物体） | 找到桌上的物品并触碰 |

### 用户创意扩展

通过 OpenClaw + Claude Opus，用户可以:

1. **录制新动作**: 手动移动机械臂 → 保存 → 回放验证
2. **编排复杂动画**: 描述动作 → AI 生成 Python 脚本 → 迭代优化
3. **视觉互动**: 识别桌面物品 → 做出反应
4. **桌面控制**: 机械臂作为外设控制鼠标、启动软件

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
