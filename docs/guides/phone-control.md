# 手机控制集成

`lampgo` 内置了 Open-AutoGLM 的手机 GUI Agent，并注册为技能 `phone_task`，让同一个 Agent 同时控制支架和夹在支架上的手机。

## 运行方式

先确保手机在 `adb devices -l` 中是 `device` 状态。无需再单独克隆或安装 Open-AutoGLM。

PowerShell 示例：

```powershell
cd <lampgo 仓库目录>

$env:PYTHONIOENCODING="utf-8"
$env:LAMPGO_LLM_API_BASE="https://api.mimomimo.com/v1"
$env:LAMPGO_LLM_MODEL="mimo-v2.5-pro"
$env:LAMPGO_LLM_FAST_MODEL="mimo-v2.5"
$env:LAMPGO_LLM_PROVIDER="mimo"
$env:LAMPGO_LLM_MESSAGE_TYPE="openai"
$env:LAMPGO_LLM_API_KEY="sk-..."
$env:LAMPGO_PHONE_ENABLED="true"
$env:LAMPGO_PHONE_ADB=""  # adb 不在 PATH 时，填 adb.exe / adb 的完整路径
$env:LAMPGO_PHONE_DEVICE_TYPE="adb"
$env:LAMPGO_PHONE_DEVICE_ID="<adb devices 中的设备 ID>"
$env:LAMPGO_PHONE_SKIP_MODEL_CHECK="true"
$env:LAMPGO_PHONE_VERIFY_RESULT="true"
$env:LAMPGO_PHONE_ARTIFACT_DIR=".lampgo\phone-artifacts"
$env:LAMPGO_PHONE_AUTO_INSTALL_ADB_KEYBOARD="true"
$env:LAMPGO_PHONE_DIRECT_CONTROL_ENABLED="true"

uv run lampgo run --web
```

然后打开 `http://127.0.0.1:8420`。

`phone_task` 不再有单独的模型配置；它统一复用 Lampgo 主模型配置：
`llm.api_base`、`llm.model` 和 `llm.api_key`。

## 直接调用

```powershell
uv run lampgo invoke phone_task task="请打开系统设置应用" max_steps=2
```

ADB 模式默认启用“直接控制优先”：简单任务会绕过模型和 GUI 视觉推理，直接调用系统底层工具完成，例如：

- 打开 App：`adb shell monkey -p <package> ...`
- 返回 / Home / 回车：`adb shell input keyevent ...`
- 点击坐标或可访问性树中的文本：`adb shell input tap ...`
- 输入文本：优先用随包携带的 ADBKeyboard 广播，失败时可关闭自动键盘后使用 `adb shell input text`

因此“打开设置”“回到桌面”“点击确定”“输入 xxx”这类单步动作会明显更快。复杂任务（例如打开 App 后继续搜索、浏览、判断页面内容）不会被直接控制误吞，会自动回退到 Open-AutoGLM GUI Agent。需要禁用快速路径时设置：

```powershell
$env:LAMPGO_PHONE_DIRECT_CONTROL_ENABLED="false"
```

`phone_task` 会默认追加安全约束：不要付款、下单、删除、发送消息或提交敏感表单。确实需要做敏感动作时，必须显式传 `allow_sensitive=true`，并且建议在外层先向用户确认。

ADB 模式下，lampgo 会随包携带 `ADBKeyboard.apk`，并在任务前尽量自动安装/启用它，用于中文输入。如果系统限制导致自动启用失败，需要手动在手机输入法设置里启用一次。

默认还会在任务结束后通过 ADB 抓取最终截图和 UIAutomator XML，返回 `observation.screenshot_path`、
`observation.screen_text` 与 `verification`。`verification.status=verified` 表示关键文本已在最终界面中出现；
`needs_review` 表示已保存截图但只能低置信度判断；`failed` 表示抓图失败、疑似空白页等明显异常。

## 自然语言联动

当 `LAMPGO_LLM_API_KEY` 也配置好后，Web/CLI 的自然语言 Agent 会自动看到 `phone_task` 工具，因此可以发复合任务：

```text
先看向手机，点头一下，然后打开手机设置。
```

推荐把手机动作说清楚一点：

```text
请打开手机系统设置。手机控制请调用 phone_task，并优先使用 Launch 操作，app 参数用 Settings。支架先做一个点头动作。
```

## 架构

```text
lampgo Web / CLI / Voice
        |
        v
LLM tool loop / SkillExecutor
        |
        +-- move_to / nod / dance / set_expression  -> 支架与灯光
        |
        +-- phone_task                              -> Lampgo 内置 Open-AutoGLM 子进程
                                                       -> ADB
                                                       -> Android 手机
```

Open-AutoGLM 的核心代码已经 vendored 到 `lampgo/vendor/open_autoglm/`，用户安装 lampgo 后即可使用，不需要额外准备 Open-AutoGLM 仓库或虚拟环境。
