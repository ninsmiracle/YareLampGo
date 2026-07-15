# 组合技能（ComposedSkill）

YareLampGo 的技能分成三类，彼此来源和可操作性不同：

| 类别 | 存储位置 | 创建方式 | 可编辑 / 删除？ | 在 Web UI 上的位置 |
|------|---------|---------|---------------|------------------|
| **出厂技能** | Python 代码 `lampgo/skills/builtin/` | 仓库内置 | ❌ | 「技能 → 出厂技能」板块 |
| **我的技能** | `~/.lampgo/skills/user/<skill_id>.json` | Codex / Web UI 运行时创建 | ✅ | 「技能 → 我的技能」板块 |
| **录制动作** | `~/.lampgo/recordings/user/<name>.csv` | 手动示教录制 | ✅ | 「录制动作」板块 |

这篇文档讲的是中间一层——**组合技能**，也就是 UI 里「我的技能」对应的那种。

---

## 什么是组合技能？

组合技能是一段 JSON 定义，内容是**按顺序执行若干步骤**。每一步可以是两种形态之一：

- **Level 1 —— 调用出厂技能**：`{"skill_id": "nod", "params": {...}}`
- **Level 2 —— 自定义关节轨迹**：`{"trajectory": {"waypoints": [...], "fps": N, "interpolation": "..."}}`

两种可以**混用**。比如「欢迎回家」= `set_expression(smiley)` → `nod × 2` → 一段自定义 yaw/pitch 摆动轨迹 → `play_recording('wave')`。

你不用写 Python、不用重启守护进程，文件落到 `~/.lampgo/skills/user/` 就会被自动识别并注册成一个正式的 skill，
立刻就能被本地 LLM、Codex、Web UI 的卡片点击等所有入口调用。

## 什么时候用 Level 2（自定义轨迹）？

**优先用 Level 1**。出厂技能（`nod` / `headshake` / `look_at` / `dance` / `idle_sway` / …）里的速度、缓动都是调校过的，能用现成的就别自己造轨迹。

只有在出厂技能**无法表达**你要的动作形态时，才上 Level 2：
- 需要多个关节同时以特定相位联动（出厂里没对应的）
- 需要一个"停留 + 微抖动"之类的非标节奏
- 需要把 yaw、pitch、wrist_roll 按某种特殊顺序编排

## 谁会创建这种技能？

主要是 **Codex**：当用户说「帮我攒一个 XX 技能」，Codex 会先调 `lampgo_list_skills` 看清楚有哪些出厂原子能力，
再通过 LampGo API 或项目文件生成组合方案。
高级用户也可以直接手写 JSON 丢进目录，点一下 Web UI 的「重新加载」就生效。

## JSON 结构

完整示例（`~/.lampgo/skills/user/welcome_home.json`）：

```json
{
  "skill_id": "welcome_home",
  "label": "欢迎回家",
  "description": "当主人到家时执行一段欢迎动作：先抬头看人，切换笑脸，再点头两下。",
  "parameters": {
    "mood": {
      "type": "str",
      "required": false,
      "default": "smiley",
      "description": "使用的 LED 表情名。"
    }
  },
  "steps": [
    { "skill_id": "look_at", "params": { "yaw": 0, "pitch": -20 } },
    { "skill_id": "set_expression", "params": { "expression": "{mood}" } },
    { "skill_id": "nod", "params": { "count": 2, "amplitude": 10 } }
  ]
}
```

### 字段说明

- `skill_id`（必填）：小写字母开头的 `[a-z][a-z0-9_]{0,63}`；不能和出厂技能重名。
- `label`（可选）：Web UI 上显示的中文名；留空会退到 `skill_id`。
- `description`（必填）：一句话描述，会作为工具描述喂给 LLM。
- `parameters`（可选）：对外暴露的参数，和出厂技能的 `ParameterSpec` 格式一致。
- `steps`（必填，非空）：有序步骤列表，上限 20 步。每一步**必须且只能**是以下两种形态之一：
  - **Level 1 调用出厂技能**：
    - `skill_id`：必须是出厂技能 id（`move_to / return_safe / nod / headshake / look_at / idle_sway / dance / set_expression / play_recording` —— `estop` 禁止出现）。
    - `params`（可选）：传给子技能的参数。字符串值可以用 `{外层参数名}` 做替换。
  - **Level 2 自定义轨迹**：
    - `trajectory.waypoints`（必填，≥ 2 个 & ≤ 50 个）：有序关键帧。每一帧：
      - `joints`（必填）：目标关节角（度），键限定在 `base_yaw / base_pitch / elbow_pitch / wrist_roll / wrist_pitch`。
        未指定的关节**保持上一帧的值**；第一帧中未指定的关节会填入**机器人当前实时姿态**——这样轨迹开头不会出现"瞬跳"。
      - `duration`（可选，秒，≥ 0）：从上一关键帧运动到本关键帧的时长。第一帧的 `duration` 会被忽略（它就是起点）。
    - `fps`（可选，默认 50，范围 [10, 100]）：帧流速率。
    - `interpolation`（可选，默认 `ease_in_out_cubic`）：缓动函数，白名单是 `linear / ease_in_out_cubic / ease_in_out_quad / ease_out_cubic / ease_in_cubic / ease_out_back`。
    - `ease_overshoot`（可选，默认 0.10，范围 [0.0, 0.5]）：仅对 `ease_out_back` 生效。

## 安全护栏

服务端在加载和保存时统一做校验（`lampgo/skills/loader.py`），下面这些会直接被拒：

**通用**
- `skill_id` 格式不对 / 和出厂技能重名；
- `steps` 为空、> 20 步；
- 一个步骤**同时**带 `skill_id` 和 `trajectory`（必须二选一）；
- 步骤里出现 `estop`（安全关机不是可组合的原语）。

**Level 1（`skill_id` 步骤）**
- 引用了未注册的 skill；
- 引用另一个组合技能（禁止组合套组合，杜绝递归）。

**Level 2（`trajectory` 步骤）**
- `waypoints` < 2 或 > 50；
- 关节名不在白名单；
- 关节角度超出硬件限幅（`DEFAULT_JOINT_LIMITS`，如 `base_yaw ∈ [-150, 150]`）；
- `fps` 不在 [10, 100]；
- `interpolation` 不在白名单；
- 总 `duration` > 30 秒。

> 注意：**单段速度超限不会被拒绝**。`generate_waypoint_frames` 会自动把该段拉长到安全范围，
> 对人类来说比"报错让你重填"体验更好。但如果你写了一个 0.01 s 要走 90° 的段，它实际会被延长到 ~0.5 s。

## 运行时行为

- 组合技能不走 `SkillExecutor` 去调用子步骤——会自我取消。它直接调 `child.execute(ctx, **step_params)`。
- 轨迹步骤在每次执行时**当场生成帧**，不是在保存时预生成——这样 `SafetyConfig.max_velocity` 的动态调整能立即生效。
- 任何一步返回 `error`，整个技能以 `step <i> (...): <message>` 的形式上抛错误。
- 外层 executor cancel / preempt 时：
  - 如果当前跑的是出厂技能步骤，会 `cancel()` 该子技能；
  - 如果当前跑的是轨迹步骤，会 `motion.stop_immediate()` 让舵机总线当场停下。

## 与 LLM agent 的关系

保存或删除一个组合技能后，服务端会立即重建 LLM 的工具列表
（`Server._refresh_llm_skill_tools`），
下一个回合 LLM 就能把这个技能当作普通 tool 调用，无需重启。

## 相关文件

- `lampgo/skills/composed.py` — ComposedSkill 类
- `lampgo/skills/loader.py` — JSON 校验 / 落盘 / 扫描
- `lampgo/skills/registry.py` — 新增 `unregister()`，只允许移除 user 级技能
- `lampgo/web/gateway.py` — `/api/skills/save`、`/api/skills/delete`、`/api/skills/reload`
- `lampgo/mcp_stdio.py` — Codex 可用的 LampGo MCP 工具入口
- `tests/test_user_skill_loader.py` + `tests/test_composed_skill.py` — 回归测试
