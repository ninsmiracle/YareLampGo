# Codex 集成

LampGo 把本机 Codex 当作复杂任务执行器。Codex 已安装并登录时，用户只需启动 LampGo：

```bash
uv run lampgo run --web
```

启动过程会自动查找 PATH、ChatGPT App 和 Codex App 中的 CLI，检查登录状态，并幂等注册 `lampgo` stdio MCP。用户不需要配置 token、端口、环境变量或编辑 `~/.codex/config.toml`。

## 用 Codex 完成首次装机

仓库还提供一个面向首次安装/装机的 `lampgo-setup` skill。它和运行时 MCP 分工不同：skill 带用户完成依赖、V2.0 硬件检查、烧录、校准、配网和配置；运行时 MCP 则让已经启动的 LampGo 接受 Codex 工具调用。

macOS / Linux：

```bash
./install-codex-skill.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-codex-skill.ps1
```

新建 Codex 任务后说：

```text
用 $lampgo-setup 帮我安装和配置 YareLampGo V2.0
```

skill 会选择纯软件、已组装成品或 DIY V2.0 路线，并在写舵机 ID、擦除烧录、首次 12V、校准和真实运动前要求明确的物理确认。skill 文件见 [`skills/lampgo-setup/SKILL.md`](../../skills/lampgo-setup/SKILL.md)。

## 通信链路

```text
LampGo -- codex exec --json --> Codex
Codex  -- stdio MCP ---------> lampgo mcp-stdio
lampgo mcp-stdio -- authenticated localhost HTTP --> LampGo daemon
LampGo -- WebSocket events --> Web console
```

复杂任务由 `AgentManager` 保存和调度。只读分析默认使用 Codex `read-only` sandbox；明确包含修改、实现、修复、创建等意图的任务使用 `workspace-write`。所有台灯动作仍由 LampGo daemon 执行，并经过原有的 SkillExecutor、MotionRuntime 和 SafetyKernel。

LampGo 聊天框是任务的主显示面，不要求 `codex exec` 创建的任务出现在 Codex Desktop 侧边栏。执行过程中，聊天框会显示 Codex 主动给用户的阶段说明，以及搜索、命令、工具调用和文件修改的简短状态；隐藏思维链、加密 reasoning、工具参数、完整终端输出和疑似密钥不会回传。最终答案会作为一条新的助手消息自动写回发起任务的原会话。

实时状态优先走 WebSocket；只要存在排队中或执行中的任务，网页还会每 2 秒读取一次 `/api/agent/tasks` 作为看门狗。WebSocket 半断开、浏览器短暂休眠或漏收完成事件时，看门狗和事件回放都能补齐任务状态并投递最终答案，无需用户手动刷新。

用户说“把 Codex 叫来”“把你大哥叫来”“交给 Codex”“让 Codex 来”等明确召唤语时，路由层会跳过快速 LLM 判断，直接把原始任务交给本机 Codex。只是在对话中提到 Codex 或“大哥”不会误触发。

## LED 任务状态

Codex 任务状态通过事件总线映射到灯板现有表情，不让 AgentManager 直接依赖硬件：

- `queued` / `running` / `cancelling` → `focused`（专注）
- `completed` → `check`（对号）
- `failed` / `cancelled` / `interrupted` → `cross`（叉号）

如果同时有多个 Codex 任务，只要还有一个任务在进行，灯板就保持“专注”；全部结束后才显示最后一个结束任务的结果。LED 网络调用在独立后台队列里串行执行，不阻塞 Codex 的进度事件或用户回复。

## 自动鉴权

LampGo 会在 `~/.lampgo/credentials.json` 中生成本机随机 token，文件权限为 `0600`。`lampgo mcp-stdio` 自己读取 token 并添加到 localhost 请求中，Codex 配置和用户 shell 都不保存 token。

## Codex 记忆

LampGo 的同步回复热路径只读取：

```text
~/.codex/memories/memory_summary.md
```

文件按 mtime 缓存在内存中，并按当前问题选择最多约 6 KB 的相关 Markdown 段落。LampGo 不在热路径读取完整 `MEMORY.md`、会话数据库、rollout 日志或推理记录。

可在 Web 配置页关闭“参考 Codex 摘要”。文件不存在或读取失败时，LampGo 会直接忽略，不影响正常回复。

## MCP 工具

- `lampgo_status`
- `lampgo_list_skills`
- `lampgo_invoke`
- `lampgo_camera_snap`
- `lampgo_ask_user`
- `lampgo_agent_tasks`

## 故障提示

Web 控制台的 Codex 页面会显示以下状态：

- `已接通`：CLI、登录和 MCP 均就绪。
- `请登录 Codex`：打开 Codex 完成登录即可。
- `未安装 Codex`：本机没有检测到 Codex CLI。
- `连接异常`：自动注册失败；详情卡会显示原因。

运行状态也会打印在 LampGo 启动横幅中。`--no-hw` 模式可用于验证完整的软件链路，不会连接真实电机或 LED。
