---
name: lampgo-setup
description: Interactively install, configure, assemble, and validate YareLampGo V2.0 with Codex. Use when the user asks to install LampGo, configure a new lamp, bring up assembled hardware, build the DIY V2 hardware, flash S3/C6, assign servo IDs, calibrate, connect Wi-Fi/LLM/voice/Codex, diagnose first startup, or says LampGo 安装、配置、复刻、装机、配网、烧录、舵机编号、校准、首次启动.
---

# LampGo V2 Setup

Guide the user from their actual starting state to a verified LampGo setup. Execute safe software work directly, but pause at physical or state-changing hardware gates.

## Language routing

- Detect the working language from the user's latest meaningful message, not from the operating-system locale, repository path, or installer output.
- If the user writes Chinese, use clear Simplified Chinese for the entire interaction.
- If the user writes English, use English for the entire interaction.
- If there is no usable language context, default to Simplified Chinese.
- An explicit language request always wins. If the user changes language later, follow the latest explicit preference immediately.
- Keep commands, paths, configuration keys, identifiers, and raw error text unchanged; explain them in the selected language.
- Do not interrupt setup just to ask for a language choice when these rules already resolve it.

## Outcomes

Complete the applicable path and report each item as `verified`, `completed but not verified`, `skipped`, or `blocked`:

- repository and dependency installation
- V2.0 hardware documentation review
- S3/C6 firmware readiness
- five-servo ID assignment and bus scan
- physical assembly and wiring checks
- calibration preservation and new calibration
- Wi-Fi, LLM, voice, and local Codex configuration
- Web startup and end-to-end validation

Do not call a screenshot, a started process, a `200` response, or a visible control button proof of end-to-end readiness by itself.

## Locate the repository

1. If the current directory contains `pyproject.toml`, `install.sh`, and `lampgo/`, use it.
2. Otherwise resolve this `SKILL.md` through symlinks. A repository-installed skill normally lives at `skills/lampgo-setup/SKILL.md`, so the repository root is two directories above the skill directory.
3. If that path is only a copied skill and no repository is present, search the current workspace for `YareLampGo`.
4. If no clone exists and the user asked for installation, clone `https://github.com/ninsmiracle/YareLampGo.git` into a user-approved workspace directory. Do not guess a destination that would overwrite an existing directory.

Before changing files, read:

- `README.md`
- `docs/getting-started/manual-hardware-setup.md` for the complete manual command flow
- `docs/hardware/v2/README.md` for hardware routes
- `docs/hardware/wiring.md` before any wiring or first power
- `docs/getting-started/quick-start.md`
- `docs/getting-started/configuration.md`
- the current `install.sh` or `install.ps1`

Read `git status --short --branch`. Preserve user changes, especially `assets/calibration/*.json`, `.env`, and local configuration. Never reset or clean the repository to make installation easier.

## Choose one route

Infer the route from the request. If it is unclear and changes the physical workflow, ask one short question.

### A. Software-only

Use when there is no LampGo hardware yet or the user wants to try the Web UI, Agent, configuration, or skills first.

Skip firmware, servo writes, assembly, real calibration, and real motion. Finish with `uv run lampgo run --web --no-hw`.

### B. Assembled V2.0 unit

Use when the user has a built unit whose servos are already numbered and whose S3/C6 firmware is already installed.

Start with read-only detection. Do not rerun servo assignment, erase flash, or overwrite calibration just because the commands exist.

### C. DIY V2.0 build

Use when the user has loose parts, a new PCB, unnumbered servos, or asks for the complete reproduction path.

Follow this order:

`install software -> assign one servo at a time -> flash S3/C6 -> assemble while unpowered -> verify 12V/+5V/GND -> scan -> calibrate -> onboard -> start`

## Interaction contract

- Begin with a compact checklist showing the selected route and current state.
- Perform read-only checks without repeatedly asking for permission.
- At every manual checkpoint, explain the exact physical condition to establish and ask the user to confirm it.
- After a command, read the real output and update the checklist. Never infer success from a command merely being launched.
- If the user must unplug, connect, hold BOOT, move a joint, inspect a voltage, or type a secret, stop at that step and wait.
- Follow the language-routing rules above. Keep commands copyable.

## Safety gates

Explicit confirmation is required immediately before each of these actions:

1. writing a persistent servo ID or bus baud rate
2. firmware upload, especially an erase flash
3. applying 12V for the first time after wiring or structural work
4. replacing or creating calibration for an existing `lamp_id`
5. enabling torque or issuing the first real motion

The confirmation must state the physical preconditions, not just ask “continue?”

Never:

- run `setup-motors` with more than one unconfigured/repeated-ID servo on the bus
- change servo wiring while 12V is present
- insert S3, C6, LED, or amplifier modules before verifying the +5V rail on a new board
- assume USB and external +5V may be applied together without backfeed protection
- run a large motion before calibration and a small-range test
- bypass LampGo's safety kernel by directly commanding the servo SDK
- print or open `~/.lampgo/credentials.json` in chat or logs
- place API keys in shell history, command arguments, repository files, or screenshots

## Phase 1: Read-only preflight

Collect and summarize:

- OS, CPU architecture, shell, and Windows/macOS/Linux version
- repository path, branch, commit, and dirty files
- available `git`, `curl`/`wget`, PowerShell, and USB/serial tooling
- whether `uv` is present; the installer remains authoritative even when it is not
- existing `~/.lampgo/config.toml` without exposing secrets
- candidate serial ports and whether another process owns them
- whether ports `8420` and `18790` already have listeners and which processes own them
- selected hardware route and physical readiness

Do not use a stale LampGo process as evidence that the current checkout works.

## Phase 2: Install software

Use the repository installer, not an improvised dependency command.

macOS/Linux:

```bash
./install.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer pins/bootstraps `uv`, prepares Python 3.12, and installs the lockfile-defined default dependency set, including voice support. Do not replace this with a generic `pip install` or an unquoted extra.

Verify at minimum:

```bash
uv --version
uv run python --version
uv run lampgo help
```

If install fails, report the failing stage and the newest log under `~/.lampgo/logs/`. Diagnose declaration, lockfile, installer behavior, runtime import, and platform constraints as one chain.

For route A, proceed directly to onboarding and no-hardware validation.

## Phase 3: Assign the five servo IDs (DIY only)

Show the mapping before any write:

| Position | Program name | ID |
| --- | --- | ---: |
| Base rotation | `base_yaw` | 1 |
| Base pitch | `base_pitch` | 2 |
| Elbow pitch | `elbow_pitch` | 3 |
| Wrist roll | `wrist_roll` | 4 |
| Wrist pitch | `wrist_pitch` | 5 |

Start one interactive PTY session with `uv run lampgo setup-motors`, adding `--port <port>` when detection is ambiguous. The command itself walks through the five positions. At every position prompt:

1. Ask the user to remove 12V.
2. Ask them to connect exactly one servo to the bus adapter.
3. Ask them to identify the intended physical position shown by the current prompt.
4. Restore power only after they confirm the single-servo condition.
5. Continue the existing PTY prompt and read back the write/verification result.
6. Remove 12V before allowing the command to advance to the next servo.

Do not relaunch the full five-servo wizard once per motor. If the session aborts partway through, inspect the IDs already written before deciding where to resume.

After all five writes, connect the completed chain and run:

```bash
uv run lampgo scan-motors --ids 1-5
```

Require one unique response for every ID. Duplicate, missing, or unstable IDs block assembly/calibration until resolved.

## Phase 4: Firmware readiness

Firmware lives in the separate `YareLampGo_esp32` repository. Inspect its current README and scripts before using commands; do not rely on copied commands if that repository has changed.

S3 typical flow:

```bash
./scripts/flash.sh --list-ports
./scripts/flash.sh --port <S3_PORT> --erase --monitor
```

Use `--erase` only for first installation or deliberate recovery, after confirmation that saved Wi-Fi/device state may be lost.

C6 typical flow:

```bash
arduino-cli compile --upload \
  --port <C6_PORT> \
  --fqbn esp32:esp32:esp32c6:FlashSize=8M \
  ESP32_C6_LCD_1_47_UART
```

Confirm S3 and C6 ports separately. A successful compile is not a successful upload; a successful upload is not proof that UART, display, audio, camera, or Wi-Fi works in the assembled unit.

## Phase 5: Assembly and electrical checkpoint

Open `docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx` for the illustrated sequence and `docs/hardware/v2/README.md` for searchable notes.

The user must confirm all of the following before first 12V power:

- V2.0 parts are not mixed with V1.0 parts
- motor cables are inside the intended channels and clear of gears, axes, and pinch points
- the support-arm cable holes are used and full joint travel does not tension the harness
- S3 GPIO43 TX crosses to C6 RX, and S3 GPIO44 RX crosses from C6 TX
- U3 LED pin order matches the physical connector orientation
- 12V and +5V rails are not swapped, all logic grounds are common, and polarity has been checked
- with logic modules removed, the power module measures +5V to GND
- fasteners/inserts are seated and no loose metal can short the PCB

The schematic PNGs are not fabrication outputs. If the user intends to order a PCB, stop and explain that Gerbers, drill files, placement data, a production BOM, and editable EDA source are not present in this release.

## Phase 6: Preserve configuration and calibrate

Run calibration from the LampGo repository root. The CLI intentionally aborts before touching hardware when launched from another directory.

Run read-only detection first:

```bash
uv run lampgo detect
uv run lampgo scan-motors --ids 1-5
uv run lampgo ping
```

Before calibration:

1. Determine the intended `device.lamp_id`.
2. Check `git status --short -- assets/calibration`.
3. If `assets/calibration/<lamp_id>.json` exists, copy it to a timestamped directory under `~/.lampgo/backups/calibration/` and report the backup path.
4. Never stage, commit, discard, or silently replace a user's calibration file.
5. Ask the user to support the mechanism, clear its motion envelope, keep joints away from hard stops, and prepare the 12V disconnect/estop.
6. Obtain explicit confirmation, then run `uv run lampgo calibrate`, adding `--port` if needed.

After calibration, read back the selected lamp ID and resulting calibration path. Validate with a small movement only after the user confirms the area is clear. Prefer `return_safe` or a small single-joint test; do not begin with a dance or full-range action.

## Phase 7: Onboard and configure

Run the interactive wizard in a PTY when available:

```bash
uv run lampgo onboard
```

Cover these sections:

- `env_check`: Python, uv, imports, and runtime readiness
- `hardware`: motor port, lamp ID, local/ESP32 camera, microphone, and network device
- `llm`: provider, base URL, model, and connectivity
- `persona_memory`: persona and memory choice
- `codex`: local Codex discovery, login, and idempotent LampGo MCP registration

Credential handling:

- Let the user type keys in the onboard prompt or Web settings page.
- Do not ask the user to paste secrets into chat.
- Do not display credential files. It is enough to verify file existence, permissions, and a redacted field-presence summary.
- Confirm `~/.lampgo/credentials.json` permissions are appropriately restricted on POSIX systems.

For a clean S3 or an erased device, guide the user through the Web Wi-Fi wizard: connect to `Lampgo-Setup-XXXX`, use the documented device-hotspot password, select the same 2.4 GHz network as the computer, then reconnect the computer and wait for device rediscovery. Never echo the user's Wi-Fi password.

For voice, verify the whole chain: installed voice dependencies, current local process/port ownership, SDK readiness, cloud/service connectivity when configured, browser relay, and the ESP32 audio endpoints. A lone `/healthz` response is insufficient.

## Phase 8: Start and verify

Software-only:

```bash
uv run lampgo run --web --no-hw
```

Real hardware:

```bash
uv run lampgo run --web
```

Verify from a separate process:

```bash
uv run lampgo status
uv run lampgo skills
```

Also confirm the current LampGo process owns port 8420, the Web page loads from the current checkout, the expected hardware is detected, and the task can invoke a safe small action. For no-hardware mode, run a text/skill routing check without claiming physical execution.

For real hardware, the final acceptance ladder is:

1. five unique servos detected
2. current calibration loaded for the selected unit
3. safe small motion completes without stall or wrong direction
4. `return_safe` completes
5. S3 network device is current and LED command is acknowledged
6. C6 display UART produces the expected update
7. camera/microphone/audio are tested only if the user selected those features
8. Codex status is connected and a read-only LampGo MCP call succeeds

Stop immediately on wrong direction, unexpected range, cable tension, stall, overheating, repeated timeouts, or supply instability.

## Final report

Return a compact table with:

- component/stage
- observed result
- evidence or command
- status (`verified`, `completed but not verified`, `skipped`, `blocked`)
- next action

Always list:

- repository path and commit
- selected route
- config and calibration paths without secrets
- any physical step the user still owes
- any feature not tested
- the exact safe restart command

Do not describe the setup as complete while any required physical confirmation, calibration, or selected feature validation remains outstanding.
