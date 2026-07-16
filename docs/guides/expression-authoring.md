# LampGo Expression Authoring

LampGo expressions are reusable compositions, not a single animation file:

- `EyeClip` is a 320x172 C6 LCD asset containing eyes only.
- `LedEffect` is either an immutable firmware effect or a safe color pixel clip
  uploaded to the S3 for a mouth, symbol, direction, or accent.
- `ExpressionPreset` references one optional eye and one optional LED effect.

At least one channel must be present. The two channels start at `t=0` and use
the same 0-1 phase over a default three-second duration. Expressions loop by
default and keep looping for the full action; callers must explicitly request
`once` when they want a one-shot micro-expression.

## Design Rules

1. Keep the C6 image black except for the eyes. Do not draw a mouth, product
   shell, LED board, text, or status UI into an eye sprite.
2. Use the front-facing 51x9 LED grid for mouths, arrows, hearts, symbols, and
   broad color accents. Twelve corner cells are outside the physical 447-pixel
   topology and are ignored by the compiler.
3. User effects use LED program version 2 (`pixel_clip`): exactly 30 ticks at
   10fps, up to 16 RGB colors including the off color, and no executable code.
4. Use 30 eye frames at 10fps and target 3.0 seconds. The accepted range is
   8-12fps and 2.5-3.5 seconds.
5. A transient composition may be previewed or played without being saved.
   An LLM must receive explicit user confirmation before saving a preset.
6. Do not emit arbitrary code, jumps, unbounded loops, or device allocations.
   Repeated frames use `ticks`; their sum must be exactly 30. Firmware v1
   templates remain available only for official and backward-compatible assets.

## Pixel Clip Format

```json
{
  "effect_id": "rainbow_smile",
  "label": "Rainbow smile",
  "role": "mouth",
  "program": {
    "version": 2,
    "type": "pixel_clip",
    "fps": 10,
    "palette": {".": "#000000", "1": "#ff3366", "2": "#00d8ff"},
    "roles": {"primary": "1", "secondary": "2"},
    "frames": [{
      "rows": [
        "...................................................",
        "...................................................",
        "...................................................",
        "...................................................",
        "..........1111111111111111111111111111111..........",
        "...................................................",
        "...................................................",
        "...................................................",
        "..................................................."
      ],
      "ticks": 30
    }]
  }
}
```

Palette symbols are `.123456789ABCDEF`; `.` is always off. The backend maps
the logical grid to physical wiring, deduplicates frames, compiles LEF1, and
rejects packages over 8KiB. `primary`, `secondary`, and `accent` palette roles
may be recolored by a preset without copying the animation.

## Capacity Contract

- Eye package: at most 256KiB.
- Current C6 partition: 5 eyes, 896KiB installed budget, 256KiB reserve.
- Product C6 partition: 10 eyes, 3MiB installed budget, 1MiB reserve.
- S3 custom LED effects: 24 files, 8KiB each, 192KiB total.
- S3 presets: 64 files, 1KiB each, 64KiB total.
- Composition brightness is capped at 96 to protect the shared power and Wi-Fi
  stability envelope. The normal default is 64.
- The device rejects writes that cross a quota. It never evicts another asset.

The S3 keeps LCD uploads only as staging data. After the C6 acknowledges size
and SHA256 validation, the S3 removes its LCD copy. The C6 writes to a temporary
file and replaces the installed eye only after validation succeeds.

## Runtime Discovery

Agents must query the live catalog before choosing ids:

```text
GET /api/eyes
GET /api/led-effects
GET /api/expression-presets
GET /api/device/expression-capabilities
GET /api/expression-catalog
```

The same catalog is atomically written to
`~/.lampgo/expression_library/llm-catalog.json` for local agents that cannot
call the HTTP API. It is a generated read-only projection, not a second source
of truth.

Play a saved preset:

```json
POST /api/expressions/play
{"preset_id":"dizzy"}
```

Play a transient composition without saving it:

```json
POST /api/expressions/play
{
  "eye_clip_id": "dizzy_eyes",
  "led_effect_id": "arrow",
  "led_params": {
    "direction": "right",
    "color": "#00ff88",
    "brightness": 64,
    "intensity": 0.8
  },
  "playback": "loop",
  "duration_ms": 3000
}
```

Save only after the user confirms:

```json
POST /api/expression-presets
{
  "name": "Dizzy right",
  "eye_clip_id": "dizzy_eyes",
  "led_effect_id": "arrow",
  "led_params": {"direction":"right","color":"#00ff88"},
  "playback": "loop",
  "duration_ms": 3000,
  "confirmed": true
}
```

The backend generates a stable `preset_id` when it is omitted. Renaming a
preset changes its display name, not the stable ids referenced by recordings.

Create and upload a user LED effect through the backend:

```text
POST /api/led-effects
POST /api/led-effects/{effect_id}/sync
```

Playback automatically compares the installed S3 SHA and performs the small
LEF1 upload when the selected user effect is missing or stale. Official effects
remain in firmware and cannot be overwritten or deleted.

The legacy `dizzy` cache is exposed without copying binary data as
`dizzy_eyes + dizzy_mouth -> dizzy preset`. Existing `clip_id=dizzy` callers
continue to work.
