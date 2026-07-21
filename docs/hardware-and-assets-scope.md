# Hardware and Asset Scope

This repository primarily publishes the YareLampGo software runtime, Web UI, CLI,
Codex integration, examples, and public documentation.

## License Boundaries

- Software source code in this repository is licensed under GPL-3.0-only unless a
  file says otherwise.
- ESP32 firmware is maintained in the separate `YareLampGo_esp32` repository and
  should declare its own license.
- Asset licensing is declared in the repository-level `ASSET_LICENSES.md`.
- `assets/lampgoGLB.glb` is a Web runtime visualization asset licensed under
  CC-BY-NC-SA-4.0 for non-commercial sharing and adaptation.
- Community-printable STL/3MF/STEP/STP files, when published, should live under
  `assets/printable/` and use CERN-OHL-W-2.0 unless a local notice says
  otherwise. The current public V2.0 assembly is in
  `assets/printable/YareLampGo_V2.0/`.
- The approved V2.0 assembly DOCX and schematic/PCB reference PNGs live under
  `docs/hardware/v2/` and use the hardware-asset terms listed in
  `ASSET_LICENSES.md`.

## Public Hardware Scope

The public hardware material should be limited to what helps users build, repair,
or understand YareLampGo without exposing supplier production details:

- BOM-level component references.
- Assembly notes and safety guidance.
- Supplier purchase links for finished parts or kits.
- Public wiring, component, and assembly notes under `docs/hardware/`.
- Early public community-printable STL/3MF/STEP/STP files when the team chooses
  to publish them.

## Hardware And ID Contributors

YareLampGo's hardware, industrial design (ID), and mechanical/structural work
includes contributions from @Yue-Xiaolong, @mnw852173-star, @tian135, and
@majiachao. Their contribution scope is acknowledged here separately from the
software package name and CLI command, which remain `lampgo`.

The following material should stay outside the public repository unless it is
separately approved for release:

- Production CAD and original supplier drawings.
- STEP/SLDASM/SLDPRT files that expose supplier-only manufacturing geometry,
  except for the explicitly published community reproduction package under
  `assets/printable/`.
- Vendor quotations, process documents, tooling details, and private part
  numbers.
- Appearance or industrial-design files intended for patent, trademark, or
  supplier-only workflows, unless they are explicitly published under
  `ASSET_LICENSES.md` or a local license notice.

## Runtime 3D Assets

`assets/lampgoGLB.glb` is a Web visualization asset for the pet panel. It is used
to display motion state and is not the preferred printable or manufacturing
source file. Its license is declared in `ASSET_LICENSES.md`. If the production
design changes or the asset is derived from restricted supplier CAD, replace it
with a simplified visualization model before public release.

## Public Hardware Documentation

Public hardware assembly and wiring material lives under `docs/hardware/`.
Current public entry points:

- `docs/hardware/README.md`
- `docs/hardware/wiring.md`
- `docs/hardware/v2/README.md`
- `docs/hardware/v2/YareLampGo_V2.0_assembly_manual.md`
- `docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx`
- `docs/hardware/v2/assembly-manual-media/`
- `docs/hardware/v2/schematics/`

## Printable Appearance And Structural Files

Public printable appearance and structural files live under `assets/printable/`.
The current V2.0 STEP AP214 assembly is
`assets/printable/YareLampGo_V2.0/YareLampGo_V2.0_assembly.step`, with an
assembly preview in the same directory. It is an approved community
reproduction/reference package, not a production-approved supplier drawing set
or a print-ready per-part STL/3MF package.

The V2.0 electrical PNGs are connection/routing references. Editable EDA
source, Gerbers, drills, placement data, a production BOM, and an electrical
test report are not part of the current public package.
