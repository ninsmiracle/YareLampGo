# YareLampGo V2.0 Source Manifest

Imported on 2026-07-21 from the approved local release folder `lampgo_v2版本开源`. Repository names are stable and descriptive; original source names and SHA-256 values are retained here for traceability.

| Original source | Repository path | Original SHA-256 | Role |
| --- | --- | --- | --- |
| `台灯0714.STEP` | `assets/printable/YareLampGo_V2.0/YareLampGo_V2.0_assembly.step` | `a60c8b63b40fb4bab887589cdc75fc13c43814913ac3284d27949861a5aca277` | Complete mechanical/electronic reference assembly |
| `组装说明文档.docx` | `docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx` | `0d8b2c995de3025d4e7982e1a7d2579b1c493d8e3ab22e2cfac3ca4611a077ec` | Illustrated BOM and assembly guide |
| `时间模式.gif` | `docs/images/readme/lampgo_v2_time_mode.gif` | `e5ccac7af60e22b28439351d57f0f7327dc082c46e828a67e1d91c8855bcc1d4` | Current V2.0 time-display and eye-expression demo |
| `海浪模式.gif` | `docs/images/readme/lampgo_v2_wave_mode.gif` | `810b1b192412105d427fba2557f2eb1509e4a118452b8d4cc2f99507db6d26f4` | Current V2.0 wave-motion and light-expression demo |
| `20260721-111629.png` | `docs/hardware/v2/schematics/01-xiao-esp32-s3-pinout.png` | `51bfd95c5c1da32ba82b3a2a71426456b17d740781b20963a146ef5401cf99e9` | XIAO ESP32-S3 signal map |
| `20260721-111639.png` | `docs/hardware/v2/schematics/02-esp32-c6-lcd-headers.png` | `8dc92be3d0d19ea9ed4e914f4ea0293752e3bdcdb90ea8bc7d42daba1c7797a6` | C6 LCD header map |
| `20260721-111643.png` | `docs/hardware/v2/schematics/03-max98357a-amplifier.png` | `c1ba65aaf1e7ee2740e3a083c5f6bf23cddce8c4d0cfb6f3907070e779185f2a` | MAX98357A and module-compatible amplifier circuit |
| `20260721-111646.png` | `docs/hardware/v2/schematics/04-led-board-connector.png` | `3d40bd00ff4a880c0bf10c27a3a151d378131652333c0a01cc783aebf64d10a9` | LED board connector |
| `20260721-111650.png` | `docs/hardware/v2/schematics/05-main-board-top.png` | `a1eaeb791f46e5053938b333d9dad7ee9af9035bc5431cbf735515bd0040bb0d` | Main-board top routing/reference view |
| `20260721-111654.png` | `docs/hardware/v2/schematics/06-main-board-bottom.png` | `5c373439e5b1655c614b87cea1bcfb17cbf1d3932b1c748764b46af1991d88a6` | Main-board bottom routing/reference view |
| `组装说明文档.docx!/word/media/image42.png` | `assets/printable/YareLampGo_V2.0/assembly-preview.png` | `d9054c009be0e5ef9d9f5706bfb0c284df787058bf5e7acf5b82f5bd88aef1a9` | Extracted assembly preview |

The public DOCX copy has SHA-256 `dc1a8b7bde0bd94ab490378cba6f75ba6086543aae21045f5fcafbe3708f9b45`. It differs from the original only because publishing metadata was scrubbed: creator/last-modifier values, WPS custom identifiers, and custom-property relationships were removed. A temporary copy produced a 13-page render pixel-identical to the source; the final public file was not opened by the renderer.

## Structural inspection

- Format: ISO 10303-21, STEP AP214 / `AUTOMOTIVE_DESIGN`.
- Exporter: SolidWorks 2022 (`SwSTEP 2.0`).
- Units: millimetres.
- Content scan: 34 `PRODUCT` records, 42 assembly occurrences, and 203 manifold solid B-reps.
- Named content includes the base, base cover, thickened support arms, motor shells, five motor references, head parts, LED board, power board, main PCB, ESP32-C6 LCD, and Seeed Studio XIAO ESP32-S3 Sense.

The STEP is a complete reference assembly. It has not been converted into print-ready per-part STL/3MF files, and the public package does not claim production tolerances.

## Document inspection

The assembly DOCX contains 13 rendered pages, 42 inline images, and one 24-row BOM-style table. It covers the assembly overview, motor shells, base electronics, arms, cable routing, head/LED/display installation, fasteners, and final appearance.

The original DOCX is preserved because most BOM names and geometry callouts are embedded in images rather than searchable text. The Markdown guides in this directory provide the searchable safety and wiring layer.

## Media inspection

- `时间模式.gif`: 640 × 854, 10 fps, 88 frames, 8.8 seconds.
- `海浪模式.gif`: 640 × 854, 10 fps, 79 frames, 7.9 seconds.

Both repository GIFs are byte-identical copies of the approved source files. They show the current assembled V2.0 unit but do not replace calibration, motion-safety, endurance, or electrical validation.

## Electrical publication boundary

The six PNGs are schematic and PCB routing/reference images only. This release does **not** include editable schematic/PCB source, Gerber/ODB++ fabrication layers, drill files, pick-and-place coordinates, a production BOM, impedance requirements, stack-up, or an electrical test report. Do not send the PNGs directly to a board house as a fabrication package.
