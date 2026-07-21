# YareLampGo V2.0 Structure

[简体中文](README.md) | English

V2.0 is the only currently maintained public hardware structure. The V1.0 package has been removed from the main line; do not mix V1.0 and V2.0 bases, arms, motor shells, or head parts.

![YareLampGo V2.0 assembly preview](YareLampGo_V2.0/assembly-preview.png)

## Files

| File | Purpose |
| --- | --- |
| `YareLampGo_V2.0/YareLampGo_V2.0_assembly.step` | Complete STEP AP214 assembly exported by SolidWorks 2022, in millimetres. |
| `YareLampGo_V2.0/assembly-preview.png` | Assembly preview extracted from the official guide. |
| [`../../docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx`](../../docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx) | Illustrated BOM, fastener, cable-routing, and assembly guide. |
| [`../../docs/hardware/v2/README.en.md`](../../docs/hardware/v2/README.en.md) | Searchable V2.0 assembly, electrical, and first-power guide. |

## Boundaries

- The STEP file is a complete assembly, not a print-ready per-part STL/3MF package. Export printable bodies or parts in CAD and verify units, tolerances, wall thickness, insert holes, and print orientation.
- Purchased components such as the S3, C6, PCB, LED board, and servos are included as reference geometry. Exclude them when exporting structural parts.
- Recalibrate after the first assembly or any structural change. Do not reuse V1.0 or another unit's calibration data.
- Source names, checksums, and publication limits are recorded in [`../../docs/hardware/v2/SOURCE_MANIFEST.md`](../../docs/hardware/v2/SOURCE_MANIFEST.md).

## License

Unless a local notice says otherwise, the public STEP and preview image use `CERN-OHL-W-2.0`. See [ASSET_LICENSES.md](../../ASSET_LICENSES.md).

This package is not a production-approved supplier drawing set. Verify dimensions, tolerances, materials, loads, cable movement, and electrical safety before fabrication.
