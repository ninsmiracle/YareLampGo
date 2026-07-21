# Manual YareLampGo V2.0 Setup, Flashing, and First Start

This guide covers the complete non-Codex path: install the software, assign five servo IDs, flash the S3 and C6, assemble while unpowered, verify power, calibrate, and start the Web console.

Codex users can use the repository's [`$lampgo-setup`](../../skills/lampgo-setup/SKILL.md) instead. It follows the same sequence, inspects the actual environment, performs safe steps, and pauses before hardware writes and first power.

## 0. Confirm the version and safety boundary

- This guide is for **YareLampGo V2.0** only. Do not mix V1 structures, wiring, or calibration files.
- Remove 12V before writing a servo ID or changing motor wiring. Only one servo may be on the bus during ID assignment.
- On a new board, remove the S3, C6, LED, and amplifier before checking that the logic rail is really +5V.
- Do not combine USB power and external +5V unless the hardware's backfeed protection has been verified.
- Support the mechanism, clear its motion envelope, and be ready to stop or remove 12V during calibration and first motion.

Keep these references open:

- [V2.0 Hardware and Assembly](../hardware/v2/README.en.md)
- [V2.0 Wiring Guide](../hardware/wiring.md)
- [GitHub-readable illustrated assembly guide (Chinese)](../hardware/v2/YareLampGo_V2.0_assembly_manual.md)
- [Original illustrated assembly DOCX download](../hardware/v2/YareLampGo_V2.0_assembly_manual.docx)

## 1. Install LampGo

Clone the repository:

```bash
git clone https://github.com/ninsmiracle/YareLampGo.git
cd YareLampGo
```

macOS / Linux:

```bash
./install.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer prepares the verified `uv` version, Python 3.12, and locked dependencies. Verify the CLI:

```bash
uv --version
uv run python --version
uv run lampgo help
```

For a software-only trial, skip the remaining hardware steps:

```bash
uv run lampgo onboard
uv run lampgo run --web --no-hw
```

## 2. Assign the five servo IDs

Assign IDs before mechanical assembly:

| Position | Program name | ID |
| --- | --- | ---: |
| Base rotation | `base_yaw` | 1 |
| Base pitch | `base_pitch` | 2 |
| Elbow pitch | `elbow_pitch` | 3 |
| Wrist roll | `wrist_roll` | 4 |
| Wrist pitch | `wrist_pitch` | 5 |

Discover the serial port, then start one complete assignment session:

```bash
uv run lampgo detect
uv run lampgo setup-motors
```

Specify the port when detection is ambiguous:

```bash
uv run lampgo setup-motors --port /dev/tty.usbmodemXXXX
```

Windows PowerShell uses the actual `COM` port:

```powershell
uv run lampgo setup-motors --port COM5
```

At every prompt, remove 12V, connect only the servo for the named position, then restore power. Do not restart the five-servo wizard separately for every motor.

Connect the full chain and require one stable response from each ID:

```bash
uv run lampgo scan-motors --ids 1-5
```

Resolve missing, duplicate, or intermittent IDs before calibration.

## 3. Flash the S3 camera/audio controller

Firmware is maintained in a separate repository. Use that repository's current README and scripts as the source of truth:

```bash
git clone https://github.com/shelly-tang/YareLampGo_esp32.git
cd YareLampGo_esp32
./scripts/flash.sh --list-ports
```

With Arduino IDE or `arduino-cli` installed, build and flash from source:

```bash
./scripts/flash.sh --port /dev/cu.usbmodemXXXX --erase --monitor
```

Without Arduino, use the included prebuilt package:

```bash
cd dist/YareLampGo_esp32-firmware
./flash.sh --prebuilt . --port /dev/cu.usbmodemXXXX --erase --monitor
```

Use `--erase` only for first installation or deliberate recovery. It clears saved Wi-Fi and device pairing. Omit it during normal updates when those settings should remain.

Windows users can flash from Arduino IDE by selecting `XIAO_ESP32S3` with OPI PSRAM. See the [firmware flashing guide](https://github.com/shelly-tang/YareLampGo_esp32) for current dependencies, prebuilt packages, and BOOT/RESET recovery.

A successful upload is not full hardware validation. Continue by checking boot logs, camera, microphone, speaker, LED, Wi-Fi, and the UART link to the C6.

## 4. Flash the C6 eye display

Connect the C6 separately and confirm its port is not the S3 port. From the firmware repository root:

```bash
arduino-cli compile --upload \
  --port /dev/cu.usbmodemYYYY \
  --fqbn esp32:esp32:esp32c6:FlashSize=8M \
  ESP32_C6_LCD_1_47_UART
```

The C6 requires the 8MB partition layout. The first migration from an older layout requires an erase and reflash and removes cached eye clips. See [`ESP32_C6_LCD_1_47_UART/README.md`](https://github.com/shelly-tang/YareLampGo_esp32/blob/main/ESP32_C6_LCD_1_47_UART/README.md) for the current migration notes.

On Windows, use the same command with the actual `COM` port when `arduino-cli` is installed, or select the ESP32-C6 and 8MB Flash in Arduino IDE before compiling and uploading.

## 5. Assemble and inspect while unpowered

Follow [V2.0 Hardware and Assembly](../hardware/v2/README.en.md) and the assembly DOCX with 12V and USB disconnected. Verify:

- Servo cables are not pinched and are not tensioned or abraded through the full joint range.
- S3 GPIO43 TX → C6 RX, and S3 GPIO44 RX ← C6 TX.
- The LED U3 pin order matches PCB markings and measured connector orientation, not wire color alone.
- The 12V motor side and +5V logic side are not swapped, and all logic grounds are common.
- Fasteners, heat-set inserts, and connectors are secure, with no loose metal near the PCB.

The schematic PNG files are wiring and review references, not board-house-ready Gerbers.

## 6. First power

1. Remove the S3, C6, LED, and amplifier, then apply 12V only to the power module.
2. Measure +5V relative to GND. Remove power immediately if polarity or voltage is wrong.
3. Power down, reinstall the logic modules, and verify the S3 and C6 separately over USB.
4. Keep the joints away from hard stops, clear the motion envelope, and support the mechanism.
5. Apply servo 12V and run read-only checks first:

```bash
cd YareLampGo
uv run lampgo detect
uv run lampgo scan-motors --ids 1-5
uv run lampgo ping
```

Do not calibrate until all five motors are stable and the direction, harness, and power checks have passed.

## 7. Calibrate, provision, and start

Run calibration from the YareLampGo repository root. Inspect `assets/calibration/` first. If the same `lamp_id` already has a file, copy it to a backup outside the repository instead of overwriting or deleting it.

```bash
uv run lampgo calibrate
```

Specify a device when needed:

```bash
uv run lampgo calibrate --port /dev/tty.usbmodemXXXX --id AL02
```

Windows example:

```powershell
uv run lampgo calibrate --port COM5 --id AL02
```

Complete onboarding and start the lamp:

```bash
uv run lampgo onboard
uv run lampgo run --web
```

Open <http://127.0.0.1:8420>. After an S3 erase flash, connect to `Lampgo-Setup-XXXX`, then use Web settings to join the same 2.4GHz Wi-Fi network as the computer.

In another terminal, check the running daemon:

```bash
uv run lampgo status
uv run lampgo skills
```

Use a small, slow movement for the first physical test:

```bash
uv run lampgo move base_yaw=5 --velocity 20
uv run lampgo invoke return_safe
```

Run `uv run lampgo estop` on abnormal motion and remove 12V if necessary. Prefer `Ctrl+C` to stop the daemon; use `uv run lampgo clear` if a process or motor torque remains.

## Command reference

```bash
uv run lampgo help
uv run lampgo <command> --help
```

The root [README](../../README.en.md) also keeps the most-used command list visible.
