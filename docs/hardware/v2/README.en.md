# YareLampGo V2.0 Hardware and Assembly

[简体中文](README.md) | English

V2.0 is the only maintained public hardware and mechanical version. Because V1.0 had very limited adoption, this release replaces the old structure and wiring entry points instead of supporting mixed V1/V2 assemblies.

![YareLampGo V2.0 assembly](../../../assets/printable/YareLampGo_V2.0/assembly-preview.png)

## Release contents

| Artifact | Purpose |
| --- | --- |
| [Manual setup, flashing, and first start](../../getting-started/manual-hardware-setup.en.md) | Complete flow from servo IDs and S3/C6 flashing through calibration and Web startup. |
| [Complete STEP assembly](../../../assets/printable/YareLampGo_V2.0/YareLampGo_V2.0_assembly.step) | Structural relationships and purchased-component reference geometry; units are millimetres. |
| [Assembly manual DOCX](YareLampGo_V2.0_assembly_manual.docx) | Illustrated BOM, insert/screw locations, cable routing, and assembly sequence. |
| [Current wiring guide](../wiring.md) | Searchable power, S3/C6, audio, LED, and servo wiring tables. |
| [`schematics/`](schematics/) | Four schematic/interface images and top/bottom PCB reference views. |
| [Source manifest](SOURCE_MANIFEST.md) | Original names, checksums, STEP/DOCX/GIF inspection results, and publication limits. |

## Current V2.0 unit demos

<p align="center">
  <img src="../../images/readme/lampgo_v2_time_mode.gif" alt="YareLampGo V2.0 time display mode" width="280">
  <img src="../../images/readme/lampgo_v2_wave_mode.gif" alt="YareLampGo V2.0 wave motion mode" width="280">
</p>

- **Time mode:** the front matrix displays the time while the C6 screen shows an eye expression, demonstrating the current display chain and V2.0 physical design.
- **Wave mode:** the head sweeps from side to side while the front light shows a blue bar, demonstrating calibrated joint motion coordinated with light and expression output.

These GIFs show the current V2.0 unit; they are not evidence of clock accuracy, full-range joint safety, endurance, or electrical certification. Every reproduced unit still needs its own servo scan, calibration, and motion-envelope check.

## V2.0 architecture

- Seeed Studio XIAO ESP32-S3 Sense for camera, audio, LED, network, and host communication.
- ESP32-C6-LCD-1.47 display connected to the S3 over UART.
- Five STS3215 bus servos using IDs 1 through 5.
- On-board MAX98357A audio design plus an H4 five-pin compatible amplifier-module footprint; CN7 drives the speaker.
- U3 LED connector with two +5V contacts, two ground contacts, and `DIN`.
- A 12V servo domain and a +5V logic/audio/LED domain with a shared ground.

## Electrical image map

| Image | What it establishes |
| --- | --- |
| [`01-xiao-esp32-s3-pinout.png`](schematics/01-xiao-esp32-s3-pinout.png) | GPIO1 BCLK, GPIO2 LRCLK, GPIO3 LED DIN, GPIO4 amplifier data, GPIO43/44 C6 UART, +5V/GND. |
| [`02-esp32-c6-lcd-headers.png`](schematics/02-esp32-c6-lcd-headers.png) | Crossed S3/C6 UART on U1 and VBUS/GND on U2. |
| [`03-max98357a-amplifier.png`](schematics/03-max98357a-amplifier.png) | MAX98357A power, I2S, shutdown pull-up, speaker output, and module-compatible header. |
| [`04-led-board-connector.png`](schematics/04-led-board-connector.png) | U3: GND, GND, DIN, +5V, +5V from pins 5 to 1. |
| [`05-main-board-top.png`](schematics/05-main-board-top.png) | Top-side placement and routing reference. |
| [`06-main-board-bottom.png`](schematics/06-main-board-bottom.png) | Bottom-side routing and copper reference. |

The PNGs are review aids, not a board-house package. They do not include editable EDA source, Gerbers, drill data, placement coordinates, stack-up, stencil data, a production BOM, or an electrical test report.

## Recommended assembly order

1. Assign servo IDs 1-5 before installation, with only one unconfigured servo connected at a time. Disconnect 12V before every cable change.
2. Prepare the M2.5 heat-set inserts/fasteners, M3 joint fasteners, and self-tapping screws shown in the DOCX. Verify lengths and counts against the actual parts.
3. Install the servo shells with internal cable routing; keep the three mid-arm cable exits consistent and free of pinch points.
4. With 12V and USB disconnected, install the power module, base servo, main board, and base fasteners. Route cables before closing the base.
5. Assemble the support arms with M3 fasteners, passing cables through the provided holes and checking the full joint travel.
6. Install the LED board and four retaining blocks, head ring, controller, C6 display/cover, speaker, and head shells.
7. Inspect every insert, fastener, connector orientation, clearance, and cable path before applying power.

The illustrated BOM contains image-only names and does not specify every fastener length. Treat it as an assembly reference, not an unchecked production purchasing BOM.

## First power and setup

1. Remove the S3, C6, LED, and amplifier loads. Verify 12V polarity and the power module's +5V output with a multimeter.
2. Power off, reinstall logic modules, and validate S3 and C6 firmware over USB separately.
3. Support the mechanism, keep joints away from stops, and be ready to remove 12V.
4. Install software and run read-only detection:

   ```bash
   ./install.sh
   uv run lampgo detect
   uv run lampgo scan-motors --ids 1-5
   uv run lampgo ping
   ```

5. After all five servos and directions are confirmed, back up any existing calibration and run:

   ```bash
   uv run lampgo calibrate
   uv run lampgo onboard
   uv run lampgo run --web
   ```

6. Open <http://127.0.0.1:8420>, finish 2.4 GHz Wi-Fi, LLM, voice, and Codex checks, then test a small motion before large motions.

Codex users can install the repository's [`lampgo-setup`](../../../skills/lampgo-setup/SKILL.md) skill. It offers software-only, assembled-unit, and DIY V2.0 routes and pauses before servo-ID writes, erase flashing, calibration, and first motion.

## V1.0 migration

- Do not mix V1.0 and V2.0 structural parts.
- Old wiring and print-layout images no longer represent the current board or structure.
- Existing software configuration may be used only as a field reference. Reconfirm ports, device identity, and calibration on the V2.0 unit.
- Never reuse V1.0 calibration as V2.0 calibration.
