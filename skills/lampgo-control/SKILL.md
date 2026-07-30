---
name: lampgo-control
description: Observe and safely operate a running YareLampGo through the registered LampGo MCP. Use when the user asks Codex to control LampGo or the desk-lamp robot, perform a physical task, inspect its camera or state, run an autonomy demo or model comparison, or says LampGo 控制、台灯机器人、机械臂任务、身体说明书、自由发挥、模型对比. Do not use for installation, firmware flashing, servo ID assignment, wiring, or calibration; use lampgo-setup instead.
---

# LampGo Control — 身体说明书

Use this skill as a body manual, not as a director's script.

It tells Codex what LampGo can observe, which safe tools exist, how to verify that a
physical action really happened, and when to stop. It must not prescribe the task's
action sequence, jokes, dialogue, or result.

## Language

- Follow the user's current language.
- For Chinese requests, use concise Simplified Chinese.
- Preserve exact tool names, skill IDs, parameter names, status values, paths, and raw errors.

## Route

This skill controls an already-running LampGo through the registered `lampgo` MCP.

Use `$lampgo-setup` instead for:

- installation or dependency repair
- firmware flashing
- servo ID assignment
- wiring or first power
- calibration or replacing a calibration file
- diagnosing first startup

Never fall back to:

- raw serial writes
- direct servo SDK calls
- direct HTTP calls that bypass the MCP
- hand-authored motor packets
- editing calibration to make a task pass

All motion must remain inside LampGo's registered skills, `MotionRuntime`, and
`SafetyKernel`.

## Operating principle

Keep these three layers separate:

1. **Body manual** — available tools, parameters, safety limits, verification rules.
2. **Task card** — one observable goal, success criteria, time and action budget.
3. **Autonomous choice** — Codex chooses observations, action order, retries, and when
   to declare success or failure.

Do not copy a complete solution into the body manual or task card.

## Step 1: Read-only preflight

Begin every control session with `lampgo_status`.

For a physical-motion task, require all of the following in the returned `result`:

- `no_hw` is `false`
- `virtual_motion` is `false`
- `hal_connected` is `true`
- `motor_startup_state` is `ready`
- `estopped` is `false`
- `hardware_error` is empty
- `is_busy` is `false`

For a camera-dependent task, also require `camera_ready=true`.

For an LED-dependent task, also require `led_ready=true`.

Classify the session before acting:

- `physical`: every physical gate passes.
- `simulated`: `no_hw=true` or `virtual_motion=true`.
- `unverified`: the daemon responds, but one or more required fields are absent.
- `blocked`: daemon unavailable, hardware error, recovery required, estopped, or busy.

Do not describe simulated, skipped, or unverified output as real hardware execution.

### Blocked states

- **Daemon unavailable:** report that LampGo is not running. Do not start a real-hardware
  daemon unless the user explicitly asks and the normal hardware preconditions are known.
- **Busy:** do not invoke another motion. A new skill may cancel the current one.
- **Estopped:** do not attempt to reset it. Ask the user to inspect the device and use the
  normal LampGo recovery flow.
- **Recovery required:** after the user confirms the mechanism is supported and the
  motion envelope is clear, allow only `return_safe` or `lampgo_estop`.
- **Hardware error or wrong direction:** use `lampgo_estop` if motion could continue,
  then ask the user to inspect the device.

If the user has not already confirmed the desk is clear and a physical movement is
expected, use `lampgo_ask_user` once before the first real motion. State that the user
must keep the 12 V disconnect or physical stop within reach.

## Step 2: Discover the live body

Call `lampgo_list_skills` at least once per session.

- Treat the live response as the source of truth.
- Use only exact returned `skill_id` and parameters.
- Do not guess a recording name, expression name, dynamic user skill, or undocumented
  parameter.
- Do not assume a skill exists because it appears in README text.

The default first-run planning set is:

- `return_safe`
- `look_at`
- `nod`
- `headshake`
- `set_expression`

Use an item only if it appears in the current live skill list. Skills that depend on
camera, ESP32, audio, or a particular recording require their corresponding readiness
gate and live parameters.

Treat `idle_sway` and `play_recording` as conditional: limit sway duration and amplitude,
and use a recording only when its exact physical motion is already known and the user
approved it for the current setup.

For a first autonomy video, do not give unrestricted `move_to` access. Prefer the
calibrated, registered primitives above. Keep each `look_at` change within 10–15 degrees
of the current pose unless the task has already been tested at a wider range. If a later
task genuinely needs direct joint targets, make the tighter task limit explicit.

## Step 3: Observe before planning

Record:

- `status_before`
- current joint positions
- current readiness flags
- one `camera_before` image when the task has a visible scene

`lampgo_camera_snap` now returns an MCP image content block plus small text metadata.
If it returns an error or no image content, treat vision as unavailable; do not infer the
scene from a Base64 string, a filename, or a previous frame.

Share only a short public plan of at most three bullets. The plan may state observable
steps such as “inspect the scene, try a safe action, verify the result.” Do not expose or
invent private chain-of-thought.

## Step 4: Act one step at a time

Use `lampgo_invoke` with:

- one exact `skill_id`
- only parameters returned by `lampgo_list_skills`
- `wait=true`

Run one mutating tool call at a time. After each call:

1. inspect the top-level `ok`
2. inspect nested `result.status`
3. inspect nested `result.data`, `result.error`, and any `note`
4. read `lampgo_status` again
5. capture another image when visual verification matters

An action counts as successfully invoked only when nested `result.status` is `ok`.

The following do **not** prove physical success:

- top-level `ok=true` with nested `status=cancelled`
- a result containing `motor not connected, skipped`
- a result containing `LED not connected, skipped`
- an accepted background call without a final result
- a tool response with no corresponding state or visual evidence

Codex chooses the action order. The skill does not require a fixed number of attempts,
except when the task card defines a time or mutating-call budget.

## Step 5: Verify the physical result

Use the task's observable success criteria.

Physical success requires all applicable evidence:

- preflight classified the run as `physical`
- nested skill result is `ok`, not cancelled or skipped
- the device remains healthy and not estopped
- joint state, camera image, LED result, or user confirmation matches the criterion
- no manual off-camera intervention occurred

If the device reports `stalled`, a warning, no progress, or a direction different from
the intended one:

- do not blindly repeat the same target
- stop issuing new movement calls
- use `lampgo_estop` immediately if motion may continue or the user says stop
- otherwise use `lampgo_ask_user` to request a physical inspection

`lampgo_estop` sets LampGo's persistent safety emergency-stop state and stops motion.
It does not reset the state. Codex must never hide or auto-clear an emergency stop.

## Step 6: Finish safely

On a normal finish:

1. invoke `return_safe` if it is present and the mechanism is healthy
2. wait for completion
3. read one final `lampgo_status`
4. report whether return-safe was verified

Do not call `return_safe` after an emergency stop, wrong-direction event, cable tension,
collision, stall, overheating, or supply instability until the user has physically
inspected and cleared the device.

## Evidence log

Keep a compact evidence table during the run:

| elapsed | observation | tool and input | nested status | observable result | decision |
| --- | --- | --- | --- | --- | --- |

For creator experiments, use these labels:

- `physical`
- `simulated`
- `unverified`
- `blocked`

Record tool inputs and returned status. Do not claim or display model chain-of-thought.
Tool calls, timestamps, images, and physical results are sufficient evidence of autonomy.

## Creator or model-comparison mode

Only when the user asks to film, compare models, demonstrate autonomy, or let the model
“自由发挥”, read:

`references/creator-experiment.md`

That reference defines the fair experiment, footage, metrics, and edit boundary. It must
not be loaded into ordinary control sessions.

## Final report

Return:

- task outcome: `achieved`, `failed`, or `blocked`
- evidence class: `physical`, `simulated`, `unverified`, or `blocked`
- the short public plan used
- actual tool-call table
- success-criterion result
- final LampGo status
- whether `return_safe` completed
- any human intervention
- any ability that was not tested

Never say “LampGo completed the task” when only a tool call was accepted or when hardware
was disconnected.
