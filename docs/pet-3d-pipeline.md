# lampgo Pet 3D Pipeline

This document records the v1 asset workflow for the Web pet. The pet is a visual
companion for lampgo motion state; it is not a promise of exact physical
kinematic equivalence.

## Source Assets

Primary source:

- `assets/lamp_pack_Go.rar`
- Main assembly candidate: `台灯装配图 2026.4.25 .SLDASM`
- Secondary assembly candidate: `台灯外观专利图档.SLDASM`

Fallback/reference source:

- `assets/lampgo.STEP`

The designer says the Pack-and-Go archive has already been split and referenced
by joint. Treat that hierarchy as the first rigging input, but still verify it
by opening the assembly in SolidWorks or FreeCAD before relying on it.

## Conversion Workflow

1. Open the Pack-and-Go archive in SolidWorks or FreeCAD and confirm the main
   assembly opens without missing references.
2. If the main assembly is broken or does not preserve useful hierarchy, try the
   secondary assembly. If both fail, use `assets/lampgo.STEP` as the fallback.
3. Export a mesh format that Blender can edit reliably.
4. In Blender, clean the model:
   - remove internal/invisible details that do not affect the pet silhouette;
   - merge tiny static fasteners into their parent link;
   - keep moving links separated by joint;
   - set each moving link origin at its intended joint pivot;
   - simplify materials for Web rendering.
5. Export `lampgo.glb` into `lampgo/web/static/assets/pet/lampgo.glb`.
6. Update `lampgo/web/static/assets/pet/rig.json` so each lampgo joint points to
   the real GLB node name, axis, direction, and zero offset.

## Runtime Contract

The Web pet reads joint state only:

- hardware mode: reads live `motion.current_state`;
- `--no-hw` mode: reads `VirtualMotionRuntime.current_state`.

The pet must not send motion commands to the physical robot in v1. Dragging or
posing the pet to control hardware is explicitly out of scope.

## Joint Mapping

The runtime joint names are fixed:

- `base_yaw`
- `base_pitch`
- `elbow_pitch`
- `wrist_roll`
- `wrist_pitch`

`rig.json` maps these names to GLB nodes. The current defaults are visual
placeholders for the generated fallback model. Replace them after real GLB
conversion.

## Blender And FreeCAD

Blender is not a lampgo runtime dependency. Users do not need it to run lampgo.

Blender is strongly recommended for development because it is the practical
place to inspect model hierarchy, fix pivots, reduce geometry, tune materials,
and export GLB. Blender alone is not enough for SolidWorks files; use FreeCAD,
OpenCascade, SolidWorks export, or a designer-provided GLB/FBX to bridge CAD
into Blender.

## Acceptance Checks

- `uv run lampgo run --web --no-hw` starts with virtual motion running.
- Invoking `nod`, `dance`, or `play_recording` changes pet pose without HAL
  writes.
- Hardware mode continues to use the existing `MotionRuntime + HAL` path.
- If `lampgo.glb` is absent, the generated fallback pet still animates.
- If Three.js cannot load, the 2D canvas fallback still animates.
