# Hardware and Asset Scope

This repository primarily publishes the LampGo software runtime, Web UI, CLI,
OpenClaw integration, examples, and public documentation.

## License Boundaries

- Software source code in this repository is licensed under GPL-3.0-only unless a
  file says otherwise.
- ESP32 firmware is maintained in the separate `esp32_lamp` repository and
  should declare its own license.
- Hardware and 3D-printing files do not automatically inherit the software
  license. If community-printable files are published, they should include a
  local license notice, with CERN-OHL-W-2.0 as the intended default.

## Public Hardware Scope

The public hardware material should be limited to what helps users build, repair,
or understand LampGo without exposing supplier production details:

- BOM-level component references.
- Assembly notes and safety guidance.
- Supplier purchase links for finished parts or kits.
- Simplified community-printable STL/3MF files when the team chooses to publish
  them.

The following material should stay outside the public repository unless it is
separately approved for release:

- Production CAD and original supplier drawings.
- STEP/SLDASM/SLDPRT files that expose exact manufacturing geometry.
- Vendor quotations, process documents, tooling details, and private part
  numbers.
- Appearance or industrial-design files intended for patent, trademark, or
  supplier-only workflows.

## Runtime 3D Assets

`assets/lampgoGLB.glb` is a Web visualization asset for the pet panel. It is used
to display motion state and is not intended to be a printable or manufacturing
source file. If the production design changes or the asset is derived from
restricted supplier CAD, replace it with a simplified visualization model before
public release.
