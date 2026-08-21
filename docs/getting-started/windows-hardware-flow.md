# Windows x64 硬件完整流程

本文是 YareLampGo V2.0 在 Windows x64 上从零开始装机、编号、校准和首次启动的完整顺序。
如果你只想先体验软件，可以跳过本文，执行 `uv run lampgo run --web --no-hw`。

## 全流程总览

不要把下表中的命令一次性整段执行。每一阶段都必须先满足“通过标准”，失败时按“停止点”处理后才能继续。

| 阶段 | 是否带电 | 执行命令或操作 | 通过标准 | 失败时停止点 |
| --- | --- | --- | --- | --- |
| 1. 软件安装 | 舵机 12V 断开；USB 可不接 | `powershell -ExecutionPolicy Bypass -File .\install.ps1 --dev`、`uv run pytest -q` | Python、LampGo CLI 和测试均通过 | 不接硬件，先修复安装或测试错误 |
| 2. COM 识别 | 先只接 USB；需要主动探测时再给单颗舵机供 12V | `uv run lampgo detect` | 唯一识别或人工确认电机适配器 COM 口 | 多个候选未确认时禁止写 ID |
| 3. 舵机写 ID | 每次只接一颗舵机；按提示切换 12V | `uv run lampgo setup-motors --auto-detect` | 五颗舵机分别写成 ID 1～5 | 任一颗失败时断开 12V，不连接下一颗 |
| 4. 编号复核 | 五颗舵机接回总线并供 12V | `uv run lampgo scan-motors --auto-detect --ids 1-5`、`uv run lampgo ping --auto-detect` | ID 1～5 各有且只有一个稳定响应 | 缺失、重复、掉线时禁止校准 |
| 5. S3/C6 固件 | 舵机 12V 断开；目标板按固件说明使用 USB | Arduino IDE 或固件仓库提供的烧录流程 | 两块板可启动，摄像头、音频、LED、UART、Wi-Fi 基础链路正常 | 板型、Flash、PSRAM 或串口不确定时禁止组装上电 |
| 6. 断电组装 | 12V 和所有 USB 全断开 | 按 V2.0 结构与接线文档组装 | 极性、针序、共地、线束和紧固件全部复核 | 任何短路、反接、夹线风险都必须先排除 |
| 7. 电源轨检查 | 先拆逻辑模块，只给电源板 12V | 万用表测量 +5V 对 GND | 电压约为设计值 +5V、极性正确、无异常发热 | 电压或极性异常立即断开 12V |
| 8. 校准 | 整机供电，运动范围清空 | 先备份，再运行 `uv run lampgo calibrate --auto-detect --id AL02` | 生成或更新 `assets/calibration/AL02.json`，过程无碰撞和硬限位 | 方向异常、卡顿、异响或掉线立即急停/断电 |
| 9. 首次启动 | 整机供电，手边保留 12V 开关 | `uv run lampgo onboard`、`uv run lampgo run --web --no-home` | `status` 显示硬件连接正常，Web 控制台可打开 | 服务、IPC 或硬件状态异常时禁止动作测试 |
| 10. 小动作验收 | 整机供电，扶稳机构 | 每个关节只移动 3°、速度 15，再 `return_safe` | 五个关节方向、幅度、线束和噪声正常 | 任一异常立即 `uv run lampgo estop`，必要时断 12V |
| 11. 外设验收 | 整机供电 | LED、时钟、摄像头、麦克风、音频命令 | 各外设单项通过且不会影响电机通信 | 串口占用、过热或供电波动时停止整机测试 |
| 12. 退出清理 | 先停止服务，再按需保留 12V 释放扭矩 | 服务窗口 `Ctrl+C`，必要时 `uv run lampgo clear` | 进程退出、端口释放、舵机不再保持扭矩 | 无法确认安全状态时直接断开 12V |

## 现场验收清单

- [ ] 安装脚本完成，`uv run lampgo --help` 正常。
- [ ] 全量软件测试通过，并记录提交版本。
- [ ] 已确认电机适配器 COM 口；多个 COM 口时没有依赖猜测。
- [ ] 五颗舵机按机械位置写入 ID 1～5。
- [ ] `scan-motors` 和 `ping` 均确认 ID 1～5 稳定在线。
- [ ] S3 和 C6 固件、板型、Flash/PSRAM 选项和各自 COM 口已记录。
- [ ] 断电状态下完成极性、共地、针序、线束和短路检查。
- [ ] 拆下逻辑模块时实测 +5V 对 GND 正常。
- [ ] 原校准文件已备份，新校准文件路径已记录。
- [ ] 首次启动使用 `--no-home`，服务和硬件状态正常。
- [ ] 五个关节的小角度动作和回安全位均通过。
- [ ] 软件急停和物理断电路径均已确认。
- [ ] LED、摄像头、麦克风、扬声器/音频链路逐项通过。
- [ ] 退出后相关进程、IPC 端口和舵机扭矩均已释放。

## 0. 安全边界

开始前请准备：

- Windows 10/11 x64、Python 3.12+ 和 PowerShell。
- 一个可断开的 12V 电源开关。
- 万用表。
- 空旷、不会夹手或碰撞的机械臂运动范围。

必须遵守：

- 改舵机线、写舵机 ID 或切换舵机时，先断开 12V。
- 写 ID 时总线上只能有一颗舵机。
- 新板先拆下 S3、C6、LED 和功放，确认 +5V 后再装逻辑模块。
- 不要在未确认防反灌设计前，同时用 USB 和外部 +5V 给同一模块供电。
- 第一次真实运动前，扶稳机构并准备随时急停或断开 12V。

## 1. 安装软件

**前置条件：** 舵机 12V 断开，PowerShell 位于仓库根目录；首次安装需要网络。

在 PowerShell 中进入项目根目录：

```powershell
Set-Location 'C:\Users\你的用户名\Downloads\YareLampGo-main'
powershell -ExecutionPolicy Bypass -File .\install.ps1 --dev
```

验证安装：

Windows 用户的主测试入口是从仓库根目录运行 `uv run pytest -q`；不要直接双击测试文件。该命令会运行项目测试，并在 Windows 环境中覆盖 Windows 兼容性测试。

```powershell
uv run python --version
uv run lampgo --help
uv run pytest -q
```

如果 `uv` 没有加入当前 PowerShell 的 PATH，可以直接使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\lampgo.exe --help
```

**通过标准：** Python 版本满足 3.12+，`lampgo --help` 能显示命令，测试无失败。

**停止条件：** 安装或测试失败时不要连接舵机总线，先保存错误输出并修复软件环境。

## 2. 自动检测 COM 串口

**前置条件：** 先只连接 USB 串口适配器；关闭 Arduino 串口监视器、串口调试器和其他 LampGo 进程。

先运行只读探测：

```powershell
uv run lampgo detect
```

探测会扫描：

- Windows `COM` 串口。
- Feetech 电机总线。
- ESP32/LED 串口。
- DirectShow 摄像头。
- 麦克风输入设备。

Windows 的蓝牙虚拟串口（`BTHENUM`，常见于“蓝牙链接上的标准串行”）默认跳过，因为它们可能在打开时长时间阻塞；如果你的电机适配器确实使用蓝牙，仍可用 `--port COMx` 显式指定。

准备进入舵机编号流程、并且需要忽略旧配置重新选择电机口时，运行电机专用路径：

```powershell
uv run lampgo setup-motors --auto-detect
```

它会在真正写入 ID 前先完成端口选择，并且仍会要求每次只接一颗舵机。这个命令属于编号流程，不要把它当成纯只读探测；如果还没准备写 ID，请只运行 `lampgo detect`。

配置舵机 ID 时，默认会自动选择电机串口：

```powershell
uv run lampgo setup-motors
```

端口选择优先级如下：

1. 命令行显式指定的 `--port COM5`。
2. 已保存的 `device.motor_port` 配置。
3. 自动扫描并探测 Feetech 响应。
4. 只有一个 COM 口但半双工探测不确定时，使用该唯一端口作为候选。

如果想忽略旧配置、强制重新扫描：

```powershell
uv run lampgo setup-motors --auto-detect
```

`--rescan` 是同义写法。扫描和校准也支持强制重扫：

```powershell
uv run lampgo scan-motors --auto-detect --ids 1-5
uv run lampgo ping --auto-detect
uv run lampgo calibrate --auto-detect --id AL02
```

如果有多个 COM 口且程序无法安全区分，`setup-motors` 会列出候选并要求你选择。自动检测不会在这种情况下盲目写 ID；也可以人工确认后执行：

```powershell
uv run lampgo setup-motors --port COM5
```

**通过标准：** 程序唯一识别电机口，或你已经通过设备管理器、插拔对比和适配器标签人工确认。

**停止条件：** 非交互命令遇到多个候选会返回失败；在确认 COM 口前不得执行任何写 ID 操作。

## 3. 给五颗舵机写入 ID

**前置条件：** 已确认电机 COM 口，准备好可快速断开的 12V，并把五颗舵机按机械位置做好标签。

对应关系：

| 机械位置 | 程序名称 | 目标 ID |
| --- | --- | ---: |
| 底座水平旋转 | `base_yaw` | 1 |
| 底座俯仰 | `base_pitch` | 2 |
| 肘部俯仰 | `elbow_pitch` | 3 |
| 手腕滚转 | `wrist_roll` | 4 |
| 手腕俯仰 | `wrist_pitch` | 5 |

启动完整编号向导：

```powershell
uv run lampgo setup-motors --auto-detect
```

每个提示都执行以下顺序：

1. 断开 12V。
2. 只连接当前提示位置的一颗舵机。
3. 确认没有第二颗舵机共用总线。
4. 恢复供电并按 ENTER。
5. 等待写入成功。
6. 再次断开 12V，才允许切换下一颗舵机。

不要为每颗舵机重新启动一次五舵机向导。

**通过标准：** 向导依次报告 `base_yaw`、`base_pitch`、`elbow_pitch`、`wrist_roll`、`wrist_pitch` 写入成功。

**停止条件：** 任一步失败都先断开 12V；不要把写入状态不明的舵机接回完整总线，也不要跳过失败项继续。

## 4. 编号后的只读验证

**前置条件：** 断电后把五颗舵机全部接回总线，检查没有重复接头或松动线缆，再恢复 12V。

五颗舵机全部接回总线后，执行：

```powershell
uv run lampgo scan-motors --auto-detect --ids 1-5
uv run lampgo ping --auto-detect
```

必须满足：

- ID 1、2、3、4、5 各有一个响应。
- 没有重复 ID。
- 没有间歇性掉线。
- 型号和通信状态正常。

缺失、重复或不稳定时，不要进入校准。

**通过标准：** 扫描结果只有 ID 1～5，`ping` 中五个配置名称全部在线且无状态错误。

**停止条件：** 任一 ID 缺失、重复、间歇掉线或报告状态错误时，保持只读排查，不校准、不运动。

## 5. 烧录 S3 和 C6 固件

**前置条件：** 舵机 12V 断开；一次只选择当前要烧录的板和 COM 口，并关闭占用串口的程序。

固件位于独立的 `YareLampGo_esp32` 仓库，烧录前以该仓库当前 README 和脚本为准。

Windows 用户可以使用 Arduino IDE：

- S3 选择 `XIAO_ESP32S3`。
- 按固件仓库要求启用 OPI PSRAM。
- C6 选择 ESP32-C6，并使用 8MB Flash 配置。
- S3 和 C6 使用不同的 COM 口。

`--erase` 只用于首次安装或明确的恢复操作。擦除会清除旧 Wi-Fi 和设备绑定。

烧录成功后还要验证：

- S3 能启动。
- 摄像头能工作。
- 麦克风和扬声器链路能工作。
- LED 能响应。
- S3/C6 UART 通信正常。
- Wi-Fi 能连接。

烧录命令成功不等于整机链路已经通过。

**通过标准：** 记录 S3/C6 的固件版本或提交号，两块板重启无异常，基础外设链路能独立工作。

**停止条件：** 板型、Flash 容量、PSRAM 模式或 COM 口不能确认时，不继续烧录或带电组装。

## 6. 断电组装和电气检查

**前置条件：** 12V 与所有 USB 全部断开，电容放电后再插拔连接器。

全程断开 12V 和 USB，按 V2.0 组装文档完成机械结构。检查：

- 舵机线不会被外壳夹住、拉紧或磨损。
- S3 GPIO43 TX → C6 RX。
- S3 GPIO44 RX ← C6 TX。
- LED U3 针序与 PCB 丝印方向一致。
- 12V 舵机侧和 +5V 逻辑侧没有接反。
- 所有逻辑地共地。
- 紧固件、热熔嵌件和连接器已经固定。
- 没有松动金属可能短路 PCB。

**通过标准：** 两人复核或逐项打勾确认针序、极性、共地、线束余量和机械紧固均正确。

**停止条件：** 任何反接、夹线、裸露导体、松动金属或无法确认的接口都必须先处理，禁止上电试错。

## 7. 首次上电

**前置条件：** 完成断电检查；万用表量程和表笔位置正确；S3、C6、LED、功放暂时拆下。

1. 拆下 S3、C6、LED 和功放，只给电源模块接入 12V。
2. 用万用表确认 +5V 对 GND 的电压和极性。
3. 断电后安装逻辑模块。
4. 分别通过 USB 确认 S3/C6 可以启动。
5. 确认机械臂远离硬限位，运动范围无人无物。
6. 接入舵机 12V。
7. 先只做只读检测：

```powershell
uv run lampgo detect
uv run lampgo scan-motors --auto-detect --ids 1-5
uv run lampgo ping --auto-detect
```

五颗舵机稳定在线后，才进入校准。

**通过标准：** +5V 极性和电压正常，逻辑模块逐一上电无异常发热，五颗舵机只读检查稳定。

**停止条件：** 电压异常、冒烟、异味、异常发热、持续掉线或 USB 反向供电迹象出现时立即断开 12V 和 USB。

## 8. 备份并校准

**前置条件：** ID 1～5 只读验证通过，运动范围清空，机构被扶稳，服务进程尚未占用电机串口。

先确认当前 Lamp ID，例如 `AL02`。如果已有同名校准文件，先备份到用户目录，不要直接覆盖：

```powershell
$backup = Join-Path $env:USERPROFILE ('.lampgo\backups\calibration\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item .\assets\calibration\* $backup -Recurse -Force
```

从项目根目录运行：

```powershell
uv run lampgo calibrate --auto-detect --id AL02
```

也可以明确指定端口：

```powershell
uv run lampgo calibrate --port COM5 --id AL02
```

校准前必须：

- 清空运动范围。
- 扶稳机构。
- 远离机械硬限位。
- 准备急停和 12V 断电。

校准结束后确认目标文件存在，并把它与备份目录一起记录：

```powershell
Get-Item .\assets\calibration\AL02.json
```

**通过标准：** 校准命令完成，目标 JSON 文件存在，重新执行 `ping` 仍全部在线，机构没有撞限位。

**停止条件：** 方向异常、突然加速、卡顿、异响、发热或掉线时立即急停；不要覆盖备份后反复尝试。

## 9. 配置和启动真实台灯

**前置条件：** 校准完成；先关闭所有直接访问电机串口的扫描、校准和串口监视器。

使用向导配置电机、LED、摄像头和麦克风：

```powershell
uv run lampgo onboard
```

硬件步骤会再次显示自动探测到的 COM 口和推荐端口。确认无误后保存。

在启动 daemon 前最后做一次只读确认；该命令结束后再启动服务：

```powershell
uv run lampgo ping --auto-detect
```

第一次启动建议跳过自动回零，先确认服务和状态：

```powershell
uv run lampgo run --web --no-home
```

如果向导没有保存端口，再显式追加 `--motor-port COM5`，把 `COM5` 换成实际端口。

另开 PowerShell：

```powershell
uv run lampgo status
uv run lampgo skills
```

daemon 运行期间不要再用 `ping`、`scan-motors`、`calibrate` 或串口监视器直接打开同一 COM 口。如需复查，先在服务窗口按 `Ctrl+C` 完整停止。

确认 `hal_connected`、`hardware_present` 和电机状态正常后，再测试自动回零。浏览器打开：

```text
http://127.0.0.1:8420
```

Windows 的本地 TCP IPC 会自动创建 `%USERPROFILE%\.lampgo\ipc-token`，用于阻止其他本地用户直接发送电机控制命令。不要共享或手工修改该文件；如果它损坏，先停止 LampGo，再删除该文件并重新启动以生成新令牌。

**通过标准：** `status`、`skills` 和 Web 控制台正常，硬件连接状态正确，日志没有端口占用或未授权 IPC 错误。

**停止条件：** 硬件未连接、COM 被占用、IPC 启动失败或配置端口不一致时，不进入动作测试。

## 10. 第一次真实动作

**前置条件：** 五颗舵机校准与状态正常，机构远离硬限位，扶稳底座，手能立即触及 12V 开关。

一次只测一个关节、小角度、低速度：

```powershell
uv run lampgo move base_yaw=3 --velocity 15
uv run lampgo invoke return_safe
```

确认方向、幅度、线束和噪声都正常后，再逐个测试其他关节：

```powershell
uv run lampgo move base_pitch=3 --velocity 15
uv run lampgo invoke return_safe

uv run lampgo move elbow_pitch=3 --velocity 15
uv run lampgo invoke return_safe

uv run lampgo move wrist_roll=3 --velocity 15
uv run lampgo invoke return_safe

uv run lampgo move wrist_pitch=3 --velocity 15
uv run lampgo invoke return_safe
```

出现错误方向、卡顿、异响、发热、线材拉紧或接近限位时：

```powershell
uv run lampgo estop
```

如果软件急停不够快，直接断开 12V。

所有小动作通过后，再单独做一次急停验收。急停触发后不要继续发送运动命令；先检查机构状态，在服务窗口按 `Ctrl+C`，必要时断开 12V，再重新启动。

**通过标准：** 每个关节只向预期方向移动约 3°，无碰撞、卡顿、异响、明显发热或线束拉扯，`return_safe` 正常。

**停止条件：** 任一关节方向、幅度或机械状态异常时停止整机动作验收，记录该关节和现象后排查校准、装配与限位。

## 11. 外设验收

**前置条件：** 电机小动作已通过；测试过程中一次只改变一个外设变量，观察供电和串口稳定性。

按实际配置逐项验收：

- LED：`uv run lampgo invoke set_expression expression=heart`
- 时钟：`uv run lampgo invoke show_clock`
- 摄像头：Web 设置中选择 `0`、`1` 等 DirectShow 索引并查看预览。
- 麦克风：`uv run lampgo detect` 查看默认输入设备。
- 音乐处理链路：`uv run lampgo invoke dance_to_music duration=5 source=synthetic amplitude=0.1 led=false`
- Windows 系统音频：启用 Stereo Mix 或虚拟回环输入后使用 `source=system`。

**通过标准：** LED/时钟、摄像头预览、麦克风枚举、合成音频动作均可重复通过；启用外设后电机不掉线。

**停止条件：** 外设导致供电波动、USB 重连、COM 消失、明显发热或电机掉线时，停止并分开排查电源和数据链路。

## 12. 退出和清理

**前置条件：** 结束所有动作，机构处于稳定姿态，确认没有正在执行的录制或校准任务。

优先在服务窗口按 `Ctrl+C`。确认已停止后，如需释放扭矩：

```powershell
uv run lampgo clear
```

如果只想查看清理结果、并且明确不停止进程也不访问电机总线，可以使用：

```powershell
uv run lampgo clear --skip-kill --skip-release
```

这两个跳过参数组合不是安全关机，也不能证明端口或扭矩已释放。正常退出验收必须使用服务窗口 `Ctrl+C`，必要时再运行不带跳过参数的 `uv run lampgo clear`。

**通过标准：** 对正常清理流程，`lampgo status` 不再连接 daemon，端口可重新启动，舵机不保持扭矩且整机可安全断电。

**停止条件：** 如果进程、端口或扭矩状态不明，直接断开 12V；不要在未知状态下插拔舵机线。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 只有一个 COM 口但提示探测不确定 | 程序会自动采用唯一端口；确认电机供电后继续。 |
| 蓝牙 COM 没有出现在候选列表 | `BTHENUM` 虚拟口默认跳过；确认适配器类型后使用 `--port COMx`。 |
| 有多个 COM 口且没有自动选中 | 在交互提示中选择电机适配器，或使用 `--port COM5`。 |
| 旧配置指向错误 COM 口 | 使用 `--auto-detect` 或删除/修改 `device.motor_port`。 |
| `Access denied` | 关闭 Arduino 串口监视器、串口调试工具和其他 LampGo 进程。 |
| 五颗舵机都无响应 | 检查 12V、总线数据线、半双工适配器方向和波特率。 |
| 摄像头无法打开 | 关闭 Teams/微信/Zoom，依次尝试摄像头索引 `0`、`1`。 |
| 音乐模式没有系统声音 | 启用 Stereo Mix 或配置 Windows 虚拟回环输入。 |

## Windows 现场测试记录模板

将下面内容复制到 Issue、PR 评论或本地测试记录中。没有实测的项目写“未测”，不要写“通过”。

### 环境

- 测试日期：
- 测试人员：
- Git 提交：
- Windows 版本与系统类型：
- PowerShell 版本：
- Python 版本：
- 安装方式：`install.ps1 --dev` / 其他：
- 全量测试结果：

### 串口与舵机

- 电机适配器型号：
- 自动检测结果：
- 最终电机 COM 口：
- 是否有多个候选：是 / 否；人工确认方法：

| 机械位置 | 程序名称 | 目标 ID | 写入结果 | 扫描结果 | Ping 结果 | 小动作方向 |
| --- | --- | ---: | --- | --- | --- | --- |
| 底座水平旋转 | `base_yaw` | 1 |  |  |  |  |
| 底座俯仰 | `base_pitch` | 2 |  |  |  |  |
| 肘部俯仰 | `elbow_pitch` | 3 |  |  |  |  |
| 手腕滚转 | `wrist_roll` | 4 |  |  |  |  |
| 手腕俯仰 | `wrist_pitch` | 5 |  |  |  |  |

### 固件与供电

- S3 型号、COM 口、固件版本/提交：
- C6 型号、COM 口、固件版本/提交：
- S3 PSRAM 设置：
- C6 Flash 设置：
- +5V 对 GND 实测值：
- 极性检查：通过 / 未通过：
- 12V、+5V 与共地检查：通过 / 未通过：
- 异常发热、异味或 USB 反灌：无 / 有，说明：

### 校准与首次启动

- Lamp ID：
- 原校准备份目录：
- 新校准文件：
- 校准结果：
- `lampgo status` 结果摘要：
- Web 控制台：通过 / 未通过：
- IPC token 文件已生成：是 / 否：

### 功能验收

| 项目 | 结果 | 记录 |
| --- | --- | --- |
| 五关节 3° 小动作 | 通过 / 未通过 / 未测 |  |
| `return_safe` | 通过 / 未通过 / 未测 |  |
| 软件急停 | 通过 / 未通过 / 未测 |  |
| 物理 12V 断电 | 通过 / 未通过 / 未测 |  |
| LED/表情 | 通过 / 未通过 / 未测 |  |
| 时钟显示 | 通过 / 未通过 / 未测 |  |
| 摄像头 | 通过 / 未通过 / 未测 |  |
| 麦克风 | 通过 / 未通过 / 未测 |  |
| 扬声器/音频链路 | 通过 / 未通过 / 未测 |  |
| Windows 系统音频 | 通过 / 未通过 / 未测 |  |
| 退出、端口和扭矩释放 | 通过 / 未通过 / 未测 |  |

### 结论

- [ ] 全部通过，可进入日常使用。
- [ ] 有条件通过，限制与待办：
- [ ] 未通过，停止阶段：
- 日志、照片或视频位置：
- 其他异常：
