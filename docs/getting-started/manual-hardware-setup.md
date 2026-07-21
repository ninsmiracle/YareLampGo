# YareLampGo V2.0 手动安装、烧录与首次启动

本文给不使用 Codex skill 的用户提供完整手动路径：安装软件、给五颗舵机编号、烧录 S3/C6、断电组装、检查供电、校准并启动 Web 控制台。

如果你使用 Codex，可以改用仓库自带的 [`$lampgo-setup`](../../skills/lampgo-setup/SKILL.md)。它执行的仍是同一套流程，只是会读取实际环境、运行安全步骤，并在硬件写入和首次上电前停下来确认。

## 0. 先确认版本和安全边界

- 本文只适用于 **YareLampGo V2.0**，不要混用 V1 结构、接线或校准文件。
- 写舵机 ID 或换舵机线前必须断开 12V；设置 ID 时总线上只能有一颗舵机。
- 新主板先移除 S3、C6、LED 和功放，确认 +5V 输出正确后再安装逻辑模块。
- 未确认防反灌设计前，不要同时使用 USB 和外部 +5V 给同一模块供电。
- 校准和首次运动时扶稳机构、清空运动范围，并准备随时急停或断开 12V。

结构、接线和首次上电检查必须配合阅读：

- [V2.0 硬件与组装](../hardware/v2/README.md)
- [V2.0 接线指南](../hardware/wiring.md)
- [图文组装说明 DOCX](../hardware/v2/YareLampGo_V2.0_assembly_manual.docx)

## 1. 安装 LampGo 软件

获取代码：

```bash
git clone https://github.com/ninsmiracle/YareLampGo.git
cd YareLampGo
```

macOS / Linux：

```bash
./install.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会准备已验证的 `uv`、Python 3.12 和锁定依赖。完成后先确认 CLI：

```bash
uv --version
uv run python --version
uv run lampgo help
```

如果你只想体验软件，可以跳过后面的硬件步骤：

```bash
uv run lampgo onboard
uv run lampgo run --web --no-hw
```

## 2. 给五颗舵机编号

组装前按下面的对应关系写入 ID：

| 位置 | 程序名称 | ID |
| --- | --- | ---: |
| 底座水平旋转 | `base_yaw` | 1 |
| 底座俯仰 | `base_pitch` | 2 |
| 肘部俯仰 | `elbow_pitch` | 3 |
| 手腕滚转 | `wrist_roll` | 4 |
| 手腕俯仰 | `wrist_pitch` | 5 |

先发现串口：

```bash
uv run lampgo detect
```

再启动一次完整编号向导：

```bash
uv run lampgo setup-motors
```

有多个串口时显式指定：

```bash
uv run lampgo setup-motors --port /dev/tty.usbmodemXXXX
```

Windows PowerShell 把端口替换成实际的 `COM` 口：

```powershell
uv run lampgo setup-motors --port COM5
```

每次按向导切换舵机时，都要先断开 12V，只连接当前要编号的一颗舵机，再恢复供电。不要为每颗舵机重新启动五舵机向导。

全部写完后连接完整总线，检查 ID 1～5 是否各有一个稳定响应：

```bash
uv run lampgo scan-motors --ids 1-5
```

缺失、重复或时有时无都要先排除，不能直接进入校准。

## 3. 烧录 S3 摄像头/音频主控

固件在独立仓库中，烧录前以该仓库的当前说明和脚本为准：

```bash
git clone https://github.com/shelly-tang/YareLampGo_esp32.git
cd YareLampGo_esp32
./scripts/flash.sh --list-ports
```

安装了 Arduino IDE 或 `arduino-cli` 时，可以从源码编译并烧录：

```bash
./scripts/flash.sh --port /dev/cu.usbmodemXXXX --erase --monitor
```

没有 Arduino 时，可使用仓库提供的预编译包：

```bash
cd dist/YareLampGo_esp32-firmware
./flash.sh --prebuilt . --port /dev/cu.usbmodemXXXX --erase --monitor
```

`--erase` 只用于第一次安装或明确的恢复操作，它会清空旧 Wi-Fi 和设备绑定。普通固件升级且需要保留配网信息时去掉 `--erase`。

Windows 用户可使用 Arduino IDE 打开源码并选择 `XIAO_ESP32S3`，同时启用 OPI PSRAM；预编译包、依赖和 BOOT/RESET 故障处理见 [固件仓库中文烧录指南](https://github.com/shelly-tang/YareLampGo_esp32/blob/main/README.zh-CN.md)。

烧录完成不代表整机链路已经通过。至少要继续验证启动日志、摄像头、麦克风、扬声器、LED、Wi-Fi 和与 C6 的 UART 通信。

## 4. 烧录 C6 眼睛屏幕

先单独连接 C6，确认它和 S3 使用的不是同一个串口。进入固件仓库根目录后运行：

```bash
arduino-cli compile --upload \
  --port /dev/cu.usbmodemYYYY \
  --fqbn esp32:esp32:esp32c6:FlashSize=8M \
  ESP32_C6_LCD_1_47_UART
```

C6 必须使用 8MB Flash 分区。旧分区第一次迁移需要擦除后重刷，会删除已经缓存的眼睛动画；具体迁移说明见固件仓库的 [`ESP32_C6_LCD_1_47_UART/README.md`](https://github.com/shelly-tang/YareLampGo_esp32/blob/main/ESP32_C6_LCD_1_47_UART/README.md)。

Windows 上安装了 `arduino-cli` 时使用同一条命令，并把 `--port` 的值换成实际 `COM` 口；也可以在 Arduino IDE 中选择 ESP32-C6 和 8MB Flash 后编译上传。

## 5. 断电组装并检查接线

按 [V2.0 硬件与组装](../hardware/v2/README.md) 和组装 DOCX 完成机械结构。全程断开 12V 和 USB，重点检查：

- 舵机线没有被外壳夹住，也不会在全行程中被拉扯或磨损。
- S3 GPIO43 TX → C6 RX，S3 GPIO44 RX ← C6 TX。
- LED U3 针序按 PCB 丝印和实测方向连接，不能只看线色。
- 12V 舵机侧与 +5V 逻辑侧没有接反，所有逻辑地共地。
- 紧固件、热熔嵌件和连接器已固定，没有松动金属可能碰到 PCB。

电路 PNG 是接线和审查参考，不是可以直接下单的 Gerber 制板包。

## 6. 首次上电

1. 移除 S3、C6、LED 和功放，只给电源模块接入 12V。
2. 用万用表测量 +5V 对 GND。极性或电压不正确时立即断电。
3. 断电后装回逻辑模块，分别通过 USB 确认 S3 和 C6 能启动。
4. 检查关节远离机械限位、运动范围无人无物，扶稳机构。
5. 再接入舵机 12V，先只运行只读检测：

```bash
cd YareLampGo
uv run lampgo detect
uv run lampgo scan-motors --ids 1-5
uv run lampgo ping
```

只有五颗舵机全部稳定在线，且方向、线束和电源均确认后，才能校准。

## 7. 校准、配网和启动

从 YareLampGo 仓库根目录运行校准。先检查 `assets/calibration/`；如果相同 `lamp_id` 已有文件，先复制到仓库外的备份目录，不要直接覆盖或删除。

```bash
uv run lampgo calibrate
```

需要指定设备时：

```bash
uv run lampgo calibrate --port /dev/tty.usbmodemXXXX --id AL02
```

Windows 示例：

```powershell
uv run lampgo calibrate --port COM5 --id AL02
```

然后完成软件配置并启动：

```bash
uv run lampgo onboard
uv run lampgo run --web
```

浏览器打开 <http://127.0.0.1:8420>。如果 S3 做过擦除烧录，先连接 `Lampgo-Setup-XXXX` 热点，再在 Web 设置中把设备接入与电脑相同的 2.4GHz Wi-Fi。

另开终端确认运行状态：

```bash
uv run lampgo status
uv run lampgo skills
```

第一次真实动作先做小角度、低速度测试，不要直接播放大幅动作：

```bash
uv run lampgo move base_yaw=5 --velocity 20
uv run lampgo invoke return_safe
```

发生异常立即运行 `uv run lampgo estop`，必要时断开 12V。退出 daemon 优先使用 `Ctrl+C`；如果进程或扭矩未释放，再运行 `uv run lampgo clear`。

## 常用命令去哪查

```bash
uv run lampgo help
uv run lampgo <command> --help
```

根目录 [README](../../README.md) 也保留了最常用命令速查。
