# Hardware Guide

[简体中文](README.md) | English

V2.0 is the only maintained public YareLampGo hardware and mechanical version. V1.0 wiring, structure, and calibration data are no longer the main line and must not be mixed with V2.0.

## Choose a hardware route first

| Route | Firmware target | Boundary |
| --- | --- | --- |
| Legacy S3 + standalone C6 display | Firmware repository root and `ESP32_C6_LCD_1_47_UART/` | C6 is the display; S3 synchronizes expressions over UART; keep `motor_transport = "serial"`. |
| P4 head + C6 Wi-Fi | Firmware repository `ESP32_P4_HEAD/` | C6 is P4's network coprocessor and must not run the display firmware; explicitly set `motor_transport = "p4"`. |

The routes coexist and must not be cross-flashed or cross-wired. See the [P4 wireless head guide](p4-wireless-head.md) for the runtime boundary.

## Current entry points

| File | Purpose |
| --- | --- |
| [V2.0 hardware and assembly](v2/README.en.md) | Release contents, six electrical/PCB image explanations, assembly order, first power, and V1 migration. |
| [V2.0 wiring](wiring.md) | Searchable 12V/+5V, S3/C6, amplifier, LED, and five-servo wiring tables. |
| [V2.0 assembly guide (web, Chinese)](v2/YareLampGo_V2.0_assembly_manual.md) | GitHub-readable BOM, wiring, fabrication, and assembly images. |
| [V2.0 assembly manual (DOCX download)](v2/YareLampGo_V2.0_assembly_manual.docx) | Original illustrated file for offline viewing or printing. |
| [V2.0 STEP structure](../../assets/printable/README.en.md) | Complete STEP AP214 assembly, preview, and usage limits. |
| [Source manifest](v2/SOURCE_MANIFEST.md) | Original names, SHA-256 values, inspection results, and publication limits. |
| [Hardware and asset scope](../hardware-and-assets-scope.md) | Licensing and publication boundary. |
| [P4 wireless head guide](p4-wireless-head.md) | P4 selection, provisioning, wireless motion, and legacy S3/C6 rollback. |

## Important boundary

The PNGs explain nets, interfaces, placement, and PCB routing; they are not Gerber, drill, placement, or production-BOM files. The complete STEP is an assembly reference, not a per-part STL/3MF package. Makers remain responsible for fabrication, electrical, and first-power engineering checks.

See [ASSET_LICENSES.md](../../ASSET_LICENSES.md) for the exact asset licenses.
