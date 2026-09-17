# P4 factory faces and capacity contract

P4 firmware `p4-head-0.3.6` adds `factory_face_version=p4-face-v1`. The 14 factory eye/mouth pairs render from code with a shared 3,000 ms phase. They occupy application flash, not the asset filesystem. The backend discovers them as immutable virtual eyes, LED effects and presets; no upload or user-home migration is required.

## Storage

The unchanged 16 MiB partition table contains two 5 MiB application slots and a 0x5F0000 (5.9375 MiB) LittleFS asset partition. NVS and OTA metadata occupy the remaining 64 KiB. The second application slot is for OTA, not expression assets.

| Resource | Contract |
| --- | --- |
| Factory eye/mouth pairs | 14, zero LittleFS asset bytes |
| Uploaded eyes | 16 files, at most 256 KiB each, 4 MiB total |
| Uploaded mouths | 24 files, at most 8 KiB each, 192 KiB total |
| Upload staging | 256 KiB per eye upload |
| Minimum free reserve | 512 KiB |
| Additional margin after the above worst case | 1 MiB, for filesystem overhead/concurrent staging/other files |

These are simultaneous quotas, not estimates from average file sizes. Counts include existing uploaded files. Each upload endpoint enforces per-file size, remaining reserve and count/byte admission. Commit validates the full LCD or LEF stream and atomically renames the temporary file. An old installed asset survives a rejected replacement. Quotas are shared across the two HTTP upload paths; temporary names are distinct.

LCD files use `LGLCD1`: RGB565, RLE, changed rectangles, 320×172, 8–30 FPS, 1–6 seconds. Size depends on content and changing pixels. A photographic animation is not guaranteed to fit simply because it is short. LED clips use LEF1: 54×9 native or compatible legacy 447-pixel topology, 10 FPS, 30 ticks, up to 16 colors. A fully changing native clip is below 8 KiB.

The existing host library retains its legacy 10-clip import default; the P4 web import path accepts 16. Legacy C6 capacity reporting and S3 behavior are unchanged. `GET /device/expression-capabilities` and `/device/status.storage` expose actual filesystem bytes and installed counts. Uploaded source images and the complete recording CSV library remain on the backend; they are not duplicated in P4 LittleFS.

## Runtime

- Eyes render into an existing RGB565 PSRAM framebuffer. Two 320×172×2 buffers total 220,160 bytes. SPI DMA completion gates buffer reuse.
- Factory eyes render at 20 FPS; LED output uses the existing 40 ms cadence. Both sample the same device start timestamp and 3-second phase. `once` holds the final phase, `loop` repeats without accumulating timing drift.
- The 486-pixel RMT symbol buffer uses 46,656 bytes of internal RAM. The LED package buffer is 8 KiB. Since 0.3.8, one uploaded compressed eye clip is cached in PSRAM (at most 256 KiB); switching or stopping releases it. Playback does not stream bytes from LittleFS.
- Full-screen eye output at 20 FPS is about 2.2 MB/s before bus overhead on the existing 40 MHz SPI link. Actual camera/audio/Wi-Fi concurrency and physical frame latency require on-device verification.
- Timing is phase coordination, not a claim of zero optical skew. The two physical buses have different update durations.
- The 15 factory recordings use `RECORDING_FACES`; recording names define the intended emotion. Explicit user preset/metadata overrides take precedence, and user recordings can shadow factory names. The motion CSV files remain unchanged. Motion playback triggers the expression and stops it afterward; motor keyframe-level synchronization is not claimed.

## Catalog

The new default shelf retains 13 useful original controls (off, rainbow, four arrows, check/cross, exclaim/question, star, music, heart) and adds 14 emotional mouths. Twenty-one older effects are removed from the P4 shelf; their IDs remain available for saved compositions and old hardware. Custom artwork is not deleted. Legacy hardware resolves new factory recording presets to semantically corresponding old LED modes.

Factory eye sprites and the LED atlas under `lampgo/web/static/factory-faces` are generated from the actual firmware C++ renderer. The standalone `index.html` provides synchronized playback, pause, scrub, and all recording mappings. It never operates the robot.

## Reproduce

From the firmware repository:

```sh
mkdir -p /tmp/p4-factory-qa
c++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -I ESP32_P4_HEAD tests/p4_factory_face_test.cpp \
  ESP32_P4_HEAD/factory_face.cpp ESP32_P4_HEAD/led_canvas.cpp \
  -o /tmp/p4-factory-qa/render
/tmp/p4-factory-qa/render /tmp/p4-factory-qa
```

From the backend repository:

```sh
.venv/bin/python tools/build_p4_factory_preview.py /tmp/p4-factory-qa lampgo/web/static/factory-faces
node tests/test_p4_factory_preview.cjs /tmp/p4-factory-qa
.venv/bin/pytest -q tests/test_p4_factory_faces.py
```

Firmware compilation and software tests do not establish physical display correctness. Install matching backend and firmware, verify `factory_face_version`, then test the three punctuation symbols, factory pairs, existing 123/Itachi/V clips, clock/ocean, and camera/audio/motion concurrency. This change does not alter partition sizes or require filesystem erasure.

## Legacy assets and deployment

Firmware 0.3.8 returns actual file hashes in asset inventories. The matching backend reuses installed eyes and mouths when hashes match, and reports failed upload phases explicitly. Local sources, hand drawings and device calibration live outside the published code; do not copy a user's home directory or credentials into either repository.

The three legacy packages `dizzy`, `cat-eyes`, and `itachi-eyes` may need a one-time vertical correction of their compiled LCD pixels and delta-rectangle Y coordinates for an inverted installation. This is an asset-specific correction, not a global renderer change: preserve the installation setting that makes the other eyes correct. Keep a backup, preserve durations, and update the package hash before syncing. Do not reapply a correction already recorded in `lcd.asset_correction`.
