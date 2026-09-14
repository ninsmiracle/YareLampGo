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

P4 是后端与固件配套发布的路线。完成初始配对后，正常 LAN 控制、媒体和资源上传使用设备签发的一次性 nonce 与 HMAC 证明；不要把这套后端和仍在传输可重放配对密钥的旧 P4 固件混用。

不要混用以下内容：

- 不要把根目录的 S3 `scripts/flash.sh` 用于 P4。
- 不要把 `ESP32_C6_LCD_1_47_UART` 烧录到 P4 方案中的 C6。
- 不要把旧 S3/C6 的接线、串口或校准假设直接用于 P4 灯头舵机。
- P4 的原生 USB 调试端口不是旧 Feetech 电机总线；P4 模式下请勿把它填到 `device.motor_port`。

要回到旧硬件，只需恢复 `motor_transport = "serial"` 和对应的 `motor_port`；不需要删除 P4 固件或覆盖旧 S3 配置。

## P4 语音稳定性（0.2.10 起）

P4 固件和后端需要配套升级。稳定通话采用 **16 kHz、单声道 PCM16**，暂不启用端侧 AEC；不以提高音质为目标。

- 麦克风最多发出 8 帧未确认数据，后端消费后逐帧返回确认。浏览器或网络阻塞时丢弃过期音频，不让 TCP 缓冲耗尽 P4 内部 DMA 内存。
- 扬声器分包上限 1920 字节，最多 4 包未确认数据；小窗口流水传输覆盖约 240 ms 延迟。固件播放队列也有明确上限，确认仅代表进入播放队列，不等于已经发声。
- 关闭供电设备的 C6 Wi-Fi modem sleep，避免省电带来的接收延迟；音频连接异常退出后清理会话状态。
- 摄像头 JPEG 按 1 KiB 非阻塞发送，低内部内存时等待，三秒内无法完成则关闭该响应；避免整张图塞入 64 KiB TCP 缓冲后拖住控制接口。拥塞时允许丢帧，不保证音视频无损并发。
- `/device/status` 提供 DMA 低水位、流控暂停计数、扬声器写入字节/非零采样和 I2S 写入失败计数，便于区分网络、播放驱动和实物发声问题。

此协议仅由 P4 握手显式协商，不修改旧 S3/C6 固件。新后端仍兼容没有声明 `ack-v1` 的旧 P4；新 P4 固件则要求配套的流控后端，防止旧发送器再次引发无界积压。麦克风、扬声器、摄像头和 AEC 的并发验收仍须分别记录，不能根据一次 HTTP 成功认定整机通过。

开发者可先关闭网页通话，运行不依赖云端模型的自动链路检查：

```bash
uv run python tools/p4_audio_probe.py --host <P4-IP> --seconds 60 --phases stalled,mic,duplex
```

`stalled` 会故意暂停读取麦克风数据，并检查窗口上限、设备状态及恢复接收；`mic` 为单独收音，`duplex` 为收音加静音播放。加上 `tone` 阶段会发出低音量测试音。工具只打印计数/电平，不保存录音、图片或凭据；凭据读取现有本地配对文件。
