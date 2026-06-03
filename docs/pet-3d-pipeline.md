# lampgo Pet 3D Pipeline

This document records the v1 asset workflow for the Web pet. The pet is a visual
companion for lampgo motion state; it is not a promise of exact physical
kinematic equivalence.

## Source Assets

The public repository keeps only the runtime visualization asset used by the Web
pet. Production CAD, supplier drawings, STEP/SLDASM/SLDPRT files, and other
manufacturing sources stay outside the public repository unless separately
approved for release.

Current public Web runtime source:

- `assets/lampgoGLB.glb`
- Served as `/assets/pet/lampgoGLB.glb`
- Uses `KHR_draco_mesh_compression`, so the browser loader must configure a
  Draco decoder.

## Conversion Workflow

1. Start from an approved visualization source, not from production CAD unless
   that CAD has been cleared for public release.
2. Export a mesh format that Blender can edit reliably, or export GLB directly
   when the hierarchy is preserved and safe to publish.
3. In Blender or in the Web soft-rig layer, clean/map the model:
   - remove internal/invisible details that do not affect the pet silhouette;
   - merge tiny static fasteners into their parent link;
   - keep moving links separated by joint;
   - set each moving link origin at its intended joint pivot;
   - simplify materials for Web rendering.
4. Serve the chosen GLB from repo-level `assets/` via the Web gateway.
5. Update `lampgo/web/static/assets/pet/rig.json` so each lampgo joint points to
   either a real GLB node or a virtual Three.js link group.

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

`rig.json` maps these names to virtual Three.js link groups. Those groups are
created at load time and matching mesh nodes are attached to them. This is a
soft rig for rigid mechanical links, not a skeleton/skin rig.

## Current GLB Status

`assets/lampgoGLB.glb` is the current runtime model for Web visualization. It is
not a printable model, supplier production drawing, or manufacturing source.

The file does not contain GLB animation clips, skeletons, or a CAD mate solver.
That is acceptable for Lampgo v1 because the Web pet already receives five
joint angles from `pet_pose`. The Web layer builds virtual rigid-link groups and
drives those groups directly, similar in spirit to the 2D pet from commit
`81c25a6`.

If the public visualization model is replaced, keep the same runtime contract:
it should be safe to serve in the open repository and should not expose
production CAD details.

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
- If `lampgoGLB.glb` is absent or Draco decoding fails, the generated fallback
  pet still animates.
- If Three.js cannot load, the 2D canvas fallback still animates.
