# LampGo Expression Authoring

LampGo expressions are reusable compositions, not a single animation file:

- `EyeClip` is a 320x172 C6 LCD asset containing eyes only.
- `LedEffect` is an S3 program for a mouth, symbol, direction, or accent.
- `ExpressionPreset` references one optional eye and one optional LED effect.

At least one channel must be present. The two channels start at `t=0` and use
the same 0-1 phase over a default three-second duration. Micro-expressions play
once unless the caller explicitly selects `loop`.

## Design Rules

1. Keep the C6 image black except for the eyes. Do not draw a mouth, product
   shell, LED board, text, or status UI into an eye sprite.
2. Use the 51x9 LED board for mouths, arrows, hearts, symbols, and broad color
   accents. LED effects are programs, never image sprite sheets.
3. Reuse parameterized effects. One `arrow` effect with a `direction` parameter
   is preferred over four copied programs.
4. Use 30 eye frames at 10fps and target 3.0 seconds. The accepted range is
   8-12fps and 2.5-3.5 seconds.
5. A transient composition may be previewed or played without being saved.
   An LLM must receive explicit user confirmation before saving a preset.
6. Do not emit arbitrary code, unbounded loops, dynamic allocation, or custom
   frame buffers. LED DSL version 1 only accepts `mouth`, `arrow`, `heart`, and
   `pulse` templates.

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
```

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
  "playback": "once",
  "duration_ms": 3000
}
```

Save only after the user confirms:

```json
POST /api/expression-presets
{
  "preset_id": "dizzy_right",
  "label": "Dizzy right",
  "eye_clip_id": "dizzy_eyes",
  "led_effect_id": "arrow",
  "led_params": {"direction":"right","color":"#00ff88"},
  "playback": "once",
  "duration_ms": 3000,
  "confirmed": true
}
```

The legacy `dizzy` cache is exposed without copying binary data as
`dizzy_eyes + dizzy_mouth -> dizzy preset`. Existing `clip_id=dizzy` callers
continue to work.
