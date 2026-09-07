# ESP32-P4 无线头部板路线（可选，不替换 S3）

这是一条与现有 **S3 + C6 显示屏 + USB 舵机总线** 并存的硬件路线。它不是把旧设备升级成 P4 的覆盖式更新：后端根据配置和设备能力选择传输层，两个固件目标、烧录命令、接线和运行时资产存储都必须保持分开。

## 先选一条路线

| 项目 | 旧硬件：S3 + C6 显示屏 | 新硬件：P4 头部板 + C6 Wi-Fi |
| --- | --- | --- |
| 固件入口 | 固件仓库根目录的 `XIAO_ESP32S3` 与 `ESP32_C6_LCD_1_47_UART/` | 固件仓库的 `ESP32_P4_HEAD/` |
| C6 的职责 | 独立小屏，由 S3 通过 UART 驱动 | P4 的网络协处理器；**不要**烧录 C6 小屏固件 |
| 电机通道 | 电脑 → USB/Feetech 串口 → 底座舵机 | 后端 → Wi-Fi → P4 → 灯头舵机 |
| 表情资产 | 后端 → S3 → C6；C6 确认显示完成 | 后端 → P4 LittleFS；P4 同步启动 LED 和 LCD |
| 后端配置 | `device.motor_transport = "serial"`（默认） | 显式设为 `device.motor_transport = "p4"` |

同一台电脑可保留旧设备的 `~/.lampgo/config.toml`。只有将 `motor_transport` 改为 `p4` 并启用对应 P4 设备后，后端才会尝试无线运动通道；它不会因为发现到 ESP32 而把旧 USB 电机总线自动改成 P4。

## P4 后端配置

先保持旧硬件断电，确认 P4 已烧录 `ESP32_P4_HEAD` 并完成配网。然后在 Web 设置页的“硬件”中保存，或在 `~/.lampgo/config.toml` 写入：

```toml
[device]
motor_transport = "p4"
lamp_id = "AL02"
p4_motion_port = 82

[device_esp32]
enabled = true
preferred_host = "lampgo-p4-XXXX.local" # 也可留空自动发现
mic_enabled = true
```

随后启动：

```bash
uv run lampgo run --web
```

设备状态应显示 `motor_transport: p4`，并且 P4 的 `/device/status` 会声明 `platform: esp32-p4`。这个能力标识决定表情素材采用 P4 直连上传；旧 S3 未声明该标识时，仍使用原有 S3→C6 协议。

## P4 固件与安全边界

P4 的构建、原生 USB、供电和第一次运动说明见固件仓库的 [`ESP32_P4_HEAD/README.md`](https://github.com/shelly-tang/YareLampGo_esp32/tree/main/ESP32_P4_HEAD)。首次切换前必须完成五关节校准、低扭矩无负载动作、LCD/LED 方向、摄像头、音频和配网验证。

不要混用以下内容：

- 不要把根目录的 S3 `scripts/flash.sh` 用于 P4。
- 不要把 `ESP32_C6_LCD_1_47_UART` 烧录到 P4 方案中的 C6。
- 不要把旧 S3/C6 的接线、串口或校准假设直接用于 P4 灯头舵机。
- P4 的原生 USB 调试端口不是旧 Feetech 电机总线；P4 模式下请勿把它填到 `device.motor_port`。

要回到旧硬件，只需恢复 `motor_transport = "serial"` 和对应的 `motor_port`；不需要删除 P4 固件或覆盖旧 S3 配置。
