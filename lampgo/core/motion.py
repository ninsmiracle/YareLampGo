"""MotionRuntime — spring-damper based motion control in a dedicated thread.

Architecture (v2 — Biomimetic)
-------------------------------
All motion sources feed a per-joint SecondOrderDynamics spring bank.
The spring bank acts as the sole trajectory generator; SafetyKernel
receives the filtered output on every tick via the unified validate_frame
path.

Command flow:

    MOVE_TO      → joint_targets[specified joints] = goal positions
    STREAM_FRAMES → joint_targets[frame joints] = current frame position
    idle          → springs hold their last settled positions

    joint_targets → SecondOrderDynamics per joint (spring bank)
                  → Overlapping Action (secondary joint coupling)
                  → SafetyKernel.validate_frame
                  → HAL.write_positions

Key design choices
------------------
* Spring state is NEVER reset on a new MOVE_TO — velocity continuity is
  preserved so back-to-back commands have no micro-stutter.
* For MOVE_TO the spring frequency is capped (cap_spring_f) so peak
  commanded velocity never exceeds the SafetyKernel hard limit; the
  damping ratio z (overshoot character) is always preserved.
* stream_frames uses a higher fixed frequency (spring_playback_f) so
  recorded CSV tracks are followed tightly while still getting the
  spring's micro-elasticity and noise rejection.
* Done detection for MOVE_TO: spring.is_settled() on all target joints,
  with a 250-tick stall detector as backup.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto

import structlog

from lampgo.core.config import MotionConfig
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.safety import SafetyKernel
from lampgo.core.spring import SecondOrderDynamics, cap_spring_f
from lampgo.core.style import get_motion_style, resolve_style_name
from lampgo.core.types import JointState, MotionStatus, MotionTarget

logger = structlog.get_logger(__name__)


class _CommandType(Enum):
    MOVE_TO = auto()
    STREAM_FRAMES = auto()
    STOP_SMOOTH = auto()
    STOP_IMMEDIATE = auto()
    SHUTDOWN = auto()


@dataclass
class _Command:
    type: _CommandType
    target: MotionTarget | None = None
    frames: list[dict[str, float]] | None = None
    fps: int = 30
    playback_mode: str = "cleaned"
    recovery: bool = False
    done_event: threading.Event | None = None


class MotionRuntime:
    """Runs a dedicated control thread at a fixed tick rate.

    The asyncio side communicates via a thread-safe command queue.
    The control thread never blocks on asyncio.
    """

    _RECOVERY_COMMAND_LEAD_DEGREES = 8.0
    _RECOVERY_FEEDBACK_ENVELOPE_TOLERANCE_DEGREES = 1.0
    _RECOVERY_MAX_FEEDBACK_VELOCITY = 60.0
    _RECOVERY_PROGRESS_EPSILON_DEGREES = 0.2
    _RECOVERY_STALL_TIMEOUT_S = 2.5
    _RECOVERY_TARGET_TOLERANCE_DEGREES = 5.0
    _STREAM_SETTLE_TIMEOUT_S = 2.0

    def __init__(
        self,
        hal: HardwareAbstraction,
        safety: SafetyKernel,
        config: MotionConfig,
    ) -> None:
        self._hal = hal
        self._safety = safety
        self._config = config

        self._tick_interval = 1.0 / config.tick_rate_hz
        self._command_queue: queue.Queue[_Command] = queue.Queue(maxsize=64)

        self._current_target: MotionTarget | None = None
        self._current_state: JointState = JointState(positions={})
        self._status = MotionStatus()

        self._running = False
        self._thread: threading.Thread | None = None
        self._recovery_target: dict[str, float] | None = None
        self._recovery_start: dict[str, float] | None = None
        self._recovery_max_velocity: float | None = None
        self._recovery_failure_reason: str | None = None
        self._recovery_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API (called from asyncio / skill side)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        # A previous stop can leave the SHUTDOWN sentinel in the queue if the
        # loop exited from `_running = False` before draining commands. Starting
        # with a fresh queue prevents the new control thread from immediately
        # consuming that stale shutdown and exiting.
        self._command_queue = queue.Queue(maxsize=64)
        self._running = True
        self._thread = threading.Thread(target=self._control_loop, name="lampgo-motion", daemon=True)
        self._thread.start()
        logger.info("motion.started", tick_hz=self._config.tick_rate_hz)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._command_queue.put(_Command(type=_CommandType.SHUTDOWN))
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("motion.stopped")

    def update_target(self, target: MotionTarget) -> None:
        """Send a new target without waiting — for real-time reactive control.

        **Use case**: visual-servo tracking where a new sensor measurement
        arrives and the target must be updated immediately (e.g. face-follow).
        Pass ``style="linear"`` for near-direct tracking (f=8 Hz, z=1.0).

        **Do NOT use** in a loop to implement scripted/parametric motions —
        use ``stream_frames()`` for that instead.
        """
        cmd = _Command(type=_CommandType.MOVE_TO, target=target)
        try:
            self._command_queue.put_nowait(cmd)
        except queue.Full:
            logger.warning("motion.queue_full, dropping oldest")
            try:
                self._command_queue.get_nowait()
            except queue.Empty:
                pass
            self._command_queue.put_nowait(cmd)

    def move_to(self, target: MotionTarget) -> threading.Event:
        """Send target and return an Event that fires when motion completes."""
        done = threading.Event()
        cmd = _Command(type=_CommandType.MOVE_TO, target=target, done_event=done)
        self._command_queue.put(cmd)
        return done

    def stream_frames(
        self, frames: list[dict[str, float]], fps: int = 30, playback_mode: str = "cleaned"
    ) -> threading.Event:
        """Queue frame-by-frame playback.  Returns a done Event.

        The spring bank tracks each incoming frame with the playback
        spring parameters (spring_playback_f / spring_playback_z),
        providing smooth micro-elasticity without re-planning.
        All safety enforcement uses validate_frame (unified path).
        """
        done = threading.Event()
        mode = (playback_mode or "cleaned").strip().lower()
        if mode not in {"raw", "cleaned", "expressive"}:
            logger.warning("motion.stream_invalid_mode_fallback", requested=mode, fallback="cleaned")
            mode = "cleaned"
        cmd = _Command(
            type=_CommandType.STREAM_FRAMES,
            frames=frames,
            fps=fps,
            playback_mode=mode,
            done_event=done,
        )
        self._command_queue.put(cmd)
        return done

    def prepare_recovery(
        self,
        target: dict[str, float],
        *,
        max_velocity: float,
        fps: int = 50,
    ) -> list[dict[str, float]]:
        """Precompute and validate a complete recovery path before torque-on."""
        with self._recovery_lock:
            return self._prepare_recovery_locked(
                target,
                max_velocity=max_velocity,
                fps=fps,
            )

    def _prepare_recovery_locked(
        self,
        target: dict[str, float],
        *,
        max_velocity: float,
        fps: int,
    ) -> list[dict[str, float]]:
        if self._running:
            raise RuntimeError("Recovery preparation requires the motion loop to be stopped.")
        if not self._hal.recovery_required:
            raise RuntimeError("Motor recovery is not required.")
        if max_velocity <= 0 or fps <= 0:
            raise RuntimeError("Recovery velocity and frame rate must be positive.")

        motor_names = self._hal.motor_names
        recovery_target = {joint: float(target[joint]) for joint in motor_names if joint in target}
        missing = [joint for joint in motor_names if joint not in recovery_target]
        if missing:
            raise RuntimeError(f"return_safe target is missing recovery joints: {missing}")

        start = self._hal.read_recovery_start()
        max_distance = max(
            (abs(recovery_target[joint] - start[joint]) for joint in motor_names),
            default=0.0,
        )
        frame_count = max(1, math.ceil(max_distance / max_velocity * fps))
        frames = [
            {
                joint: start[joint] + (recovery_target[joint] - start[joint]) * ((index + 1) / frame_count)
                for joint in motor_names
            }
            for index in range(frame_count)
        ]

        self._validate_recovery_frames(
            start,
            recovery_target,
            frames,
            fps=fps,
            max_velocity=max_velocity,
        )
        verified_start = self._hal.prepare_recovery(frames)
        self._current_state = JointState(positions=verified_start)
        self._recovery_start = dict(verified_start)
        self._recovery_target = recovery_target
        self._recovery_max_velocity = max_velocity
        self._recovery_failure_reason = None
        self.start()
        logger.info(
            "motion.recovery_prepared",
            frame_count=len(frames),
            fps=fps,
            max_velocity=max_velocity,
            max_command_lead=self._RECOVERY_COMMAND_LEAD_DEGREES,
            stall_timeout_s=self._RECOVERY_STALL_TIMEOUT_S,
            target=recovery_target,
        )
        return frames

    def stream_recovery_frames(self, frames: list[dict[str, float]], fps: int = 50) -> threading.Event:
        if not self._running or self._recovery_target is None:
            raise RuntimeError("Recovery has not been prepared.")
        done = threading.Event()
        self._command_queue.put(
            _Command(
                type=_CommandType.STREAM_FRAMES,
                frames=frames,
                fps=fps,
                playback_mode="raw",
                recovery=True,
                done_event=done,
            )
        )
        return done

    def complete_recovery(self) -> None:
        with self._recovery_lock:
            if self._running:
                self.stop()
            self._hal.complete_recovery()
            self._recovery_start = None
            self._recovery_target = None
            self._recovery_max_velocity = None
            self._recovery_failure_reason = None
            self.start()

    def abort_recovery(self) -> None:
        with self._recovery_lock:
            if self._running:
                self.stop()
            self._hal.abort_recovery()
            self._recovery_start = None
            self._recovery_target = None
            self._recovery_max_velocity = None

    def _validate_recovery_frames(
        self,
        start: dict[str, float],
        target: dict[str, float],
        frames: list[dict[str, float]],
        *,
        fps: int,
        max_velocity: float,
    ) -> None:
        validated_target = self._safety.validate_target(
            JointState(positions=start),
            MotionTarget(joints=target, anticipation=False),
        )
        if not isinstance(validated_target, MotionTarget) or any(
            abs(validated_target.joints.get(joint, float("inf")) - value) > 1e-9 for joint, value in target.items()
        ):
            raise RuntimeError("return_safe target is outside normal software safety limits.")

        previous = JointState(positions=dict(start))
        dt = 1.0 / fps
        for index, frame in enumerate(frames):
            checked = self._safety.validate_recovery_frame(
                previous,
                frame,
                target,
                dt,
                max_velocity=max_velocity,
            )
            if any(abs(checked.get(joint, float("inf")) - value) > 1e-9 for joint, value in frame.items()):
                raise RuntimeError(f"return_safe recovery frame {index} failed monotonic or velocity validation.")
            previous = JointState(positions=dict(frame))

        if not frames or any(
            abs(frames[-1].get(joint, float("inf")) - value) > 1e-9 for joint, value in target.items()
        ):
            raise RuntimeError("return_safe recovery path does not end at the verified target.")

    def stop_smooth(self) -> None:
        self._command_queue.put(_Command(type=_CommandType.STOP_SMOOTH))

    def stop_immediate(self) -> None:
        self._command_queue.put(_Command(type=_CommandType.STOP_IMMEDIATE))

    @property
    def status(self) -> MotionStatus:
        return self._status

    @property
    def current_state(self) -> JointState:
        return self._current_state

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def recovery_required(self) -> bool:
        return self._hal.recovery_required

    @property
    def recovery_error(self) -> str | None:
        return self._recovery_failure_reason

    # ------------------------------------------------------------------
    # Control thread
    # ------------------------------------------------------------------

    def _control_loop(self) -> None:
        """Strict-tick control loop running in a dedicated thread."""
        logger.info("motion.control_loop.start")

        # --- Initial hardware read ---
        try:
            self._current_state = self._hal.read_positions()
            self._safety.report_bus_health(True)
        except Exception:
            logger.exception("motion.initial_read_failed")
            self._safety.report_bus_health(False)

        dt = self._tick_interval

        # --- Spring bank (per-joint, initialised to hardware positions) ---
        _springs: dict[str, SecondOrderDynamics] = {
            j: SecondOrderDynamics(
                self._config.spring_playback_f,
                self._config.spring_playback_z,
                initial=self._current_state.get(j, 0.0),
            )
            for j in self._current_state.positions
        }
        # Current spring mode params (updated on each new command)
        _spring_f = self._config.spring_playback_f
        _spring_z = self._config.spring_playback_z

        # --- Overlapping Action state ---
        _overlap_prev: dict[str, float] = dict(self._current_state.positions)
        # Circular buffers: key = "primary→secondary", value = list of deltas
        _overlap_buffers: dict[str, list[float]] = {}
        _last_safe_frame: dict[str, float] = dict(self._current_state.positions)

        # --- Streaming state ---
        _active_done: threading.Event | None = None
        _stream_frames: list[dict[str, float]] = []
        _stream_idx = 0
        _stream_fps = 30
        _stream_accumulator = 0.0
        # Holds the most recently active stream frame (spring tracks this
        # continuously, even between frame-rate ticks)
        _current_stream_target: dict[str, float] = {}
        # After all frames are consumed, keep feeding the last target until
        # the spring has settled before signalling done.
        _stream_settling = False
        _stream_settle_timeout = 0.0
        _stream_passthrough = False
        _stream_enable_overlap = self._config.overlapping_action
        _stream_clip_window_start = time.monotonic()
        _stream_clipped_joints_in_window: set[str] = set()
        _stream_clip_events_in_window = 0

        # Recovery feedback watchdog. Each joint must keep making measurable
        # progress, remain inside its verified start-to-target envelope, and
        # never accelerate far beyond the guarded command profile.
        _recovery_best_error: dict[str, float] = {}
        _recovery_last_progress_at: dict[str, float] = {}
        _recovery_previous_feedback: dict[str, float] = {}
        _recovery_previous_feedback_at = time.monotonic()
        _recovery_last_warning_at = 0.0

        # --- Move-to completion tracking ---
        _initial_distance = 0.0
        _stall_ticks = 0
        _prev_hw_remaining = -1.0

        # --- Anticipation state ---
        # Holds the REAL final target while the spring briefly windup-moves
        # to the opposite direction.  None means no anticipation in progress.
        _anticipation_final_target: MotionTarget | None = None
        _anticipation_ticks_remaining: int = 0

        # LAMPGO_DIAG=1 enables per-tick diagnostics
        _diag_mode: bool = os.environ.get("LAMPGO_DIAG", "0").strip() == "1"
        _diag_counter = 0

        while self._running:
            t0 = time.monotonic()

            # --- Drain command queue ---
            while True:
                try:
                    cmd = self._command_queue.get_nowait()
                except queue.Empty:
                    break

                if cmd.type == _CommandType.SHUTDOWN:
                    self._running = False
                    if _active_done:
                        _active_done.set()
                    return

                elif cmd.type == _CommandType.STOP_IMMEDIATE:
                    self._current_target = None
                    _stream_frames = []
                    _current_stream_target = {}
                    _stream_passthrough = False
                    _stream_enable_overlap = self._config.overlapping_action
                    _anticipation_final_target = None
                    _anticipation_ticks_remaining = 0
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                    self._status = MotionStatus(is_done=True)

                elif cmd.type == _CommandType.STOP_SMOOTH:
                    self._current_target = None
                    _stream_frames = []
                    _current_stream_target = {}
                    _stream_passthrough = False
                    _stream_enable_overlap = self._config.overlapping_action
                    _anticipation_final_target = None
                    _anticipation_ticks_remaining = 0
                    # Springs decelerate naturally — no hard stop

                elif cmd.type == _CommandType.MOVE_TO:
                    validated = self._safety.validate_target(self._current_state, cmd.target)
                    if isinstance(validated, MotionTarget):
                        _stream_frames = []
                        _current_stream_target = {}

                        # Resolve style → spring params
                        style_key = resolve_style_name(validated.style, self._config.default_style)
                        style = get_motion_style(style_key, self._config.default_style)

                        # Cap f to stay within velocity budget; preserve z
                        v_limit = (validated.max_velocity or self._config.default_max_velocity) * 0.9
                        max_dist = max(
                            (abs(v - self._current_state.get(k, v)) for k, v in validated.joints.items()),
                            default=0.0,
                        )
                        effective_f = cap_spring_f(style.f, style.z, max_dist, v_limit)
                        _spring_f = effective_f
                        _spring_z = style.z

                        # Update spring params without resetting velocity
                        for joint in validated.joints:
                            if joint in _springs:
                                _springs[joint].set_params(effective_f, style.z)
                            else:
                                _springs[joint] = SecondOrderDynamics(
                                    effective_f,
                                    style.z,
                                    initial=self._current_state.get(joint, 0.0),
                                )

                        if _active_done:
                            _active_done.set()
                        _active_done = cmd.done_event
                        _initial_distance = sum(
                            abs(v - self._current_state.get(k, v)) for k, v in validated.joints.items()
                        )
                        _stall_ticks = 0
                        _prev_hw_remaining = -1.0

                        # --- Anticipation: windup in the opposite direction ---
                        # Only for large moves and when not already in motion.
                        ant_cfg = self._config
                        if (
                            ant_cfg.anticipation_enabled
                            and validated.anticipation is not False
                            and max_dist >= ant_cfg.anticipation_threshold
                            and all(abs(_springs[j].velocity) < 5.0 for j in validated.joints if j in _springs)
                        ):
                            ratio = ant_cfg.anticipation_ratio
                            windup_joints: dict[str, float] = {}
                            for joint, goal in validated.joints.items():
                                current = self._current_state.get(joint, goal)
                                direction = goal - current
                                windup_joints[joint] = current - direction * ratio
                            # Clamp windup joints through safety (position limits)
                            windup_target = MotionTarget(
                                joints=windup_joints,
                                max_velocity=validated.max_velocity,
                                style=validated.style,
                                anticipation=False,
                            )
                            windup_validated = self._safety.validate_target(self._current_state, windup_target)
                            if isinstance(windup_validated, MotionTarget):
                                _anticipation_final_target = validated
                                _anticipation_ticks_remaining = max(
                                    1,
                                    round(ant_cfg.anticipation_duration_ms / 1000.0 / self._tick_interval),
                                )
                                self._current_target = windup_validated
                                logger.debug(
                                    "motion.anticipation_start",
                                    windup=windup_joints,
                                    ticks=_anticipation_ticks_remaining,
                                )
                            else:
                                # Windup out of range — skip anticipation
                                _anticipation_final_target = None
                                _anticipation_ticks_remaining = 0
                                self._current_target = validated
                        else:
                            _anticipation_final_target = None
                            _anticipation_ticks_remaining = 0
                            self._current_target = validated

                        self._status = MotionStatus(target=validated, progress=0.0, is_done=False)
                        logger.info(
                            "motion.move_accepted",
                            target=validated.joints,
                            vel=validated.max_velocity,
                            style=style_key,
                            effective_f=round(effective_f, 2),
                            z=style.z,
                            anticipation=_anticipation_ticks_remaining > 0,
                        )
                    else:
                        logger.warning(
                            "motion.target_rejected",
                            reason=getattr(validated, "reason", ""),
                        )
                        if cmd.done_event:
                            cmd.done_event.set()

                elif cmd.type == _CommandType.STREAM_FRAMES:
                    if cmd.recovery and self._recovery_target is None:
                        logger.error("motion.recovery_stream_rejected_without_preflight")
                        if cmd.done_event:
                            cmd.done_event.set()
                        continue
                    _stream_frames = cmd.frames or []
                    _stream_idx = 0
                    _stream_accumulator = 0.0
                    _stream_fps = cmd.fps or 30
                    cmd_mode = cmd.playback_mode or "cleaned"
                    _stream_passthrough = cmd_mode == "raw"
                    _stream_enable_overlap = self._config.overlapping_action and cmd_mode == "expressive"
                    _current_stream_target = dict(_stream_frames[0]) if _stream_frames else {}
                    self._current_target = None
                    _anticipation_final_target = None
                    _anticipation_ticks_remaining = 0

                    # Switch springs to playback mode for cleaned / expressive.
                    # Raw mode preserves CSV frames and bypasses spring tracking
                    # on streaming joints.
                    pf = self._config.spring_playback_f
                    pz = self._config.spring_playback_z
                    _spring_f = pf
                    _spring_z = pz
                    all_frame_joints: set[str] = set()
                    for frame in _stream_frames:
                        all_frame_joints.update(frame.keys())
                    for joint in all_frame_joints:
                        if joint not in _springs:
                            _springs[joint] = SecondOrderDynamics(
                                pf,
                                pz,
                                initial=self._current_state.get(joint, 0.0),
                            )
                        if not _stream_passthrough:
                            _springs[joint].set_params(pf, pz)

                    if _active_done:
                        _active_done.set()
                    _active_done = cmd.done_event
                    _stream_settling = False
                    self._status = MotionStatus(progress=0.0, is_done=False)
                    if cmd.recovery and self._recovery_target is not None:
                        now = time.monotonic()
                        _recovery_best_error = {
                            joint: abs(self._current_state.get(joint, goal) - goal)
                            for joint, goal in self._recovery_target.items()
                        }
                        _recovery_last_progress_at = {joint: now for joint in self._recovery_target}
                        _recovery_previous_feedback = dict(self._current_state.positions)
                        _recovery_previous_feedback_at = now

            # --- Read hardware state ---
            try:
                self._current_state = self._hal.read_positions()
                self._safety.report_bus_health(True)
            except Exception:
                self._safety.report_bus_health(False)
                self._tick_sleep(t0)
                continue

            if self._recovery_target is not None and self._recovery_start is not None:
                now = time.monotonic()
                # A stream command can be dequeued immediately before the next
                # bus sample. Use at least one control interval so one encoder
                # count of normal quantization cannot look like a huge speed.
                feedback_elapsed = max(now - _recovery_previous_feedback_at, dt)
                feedback_warnings: list[str] = []
                stalled_joints: list[str] = []

                for joint, goal in self._recovery_target.items():
                    start = self._recovery_start[joint]
                    actual = self._current_state.get(joint, start)
                    envelope_min = min(start, goal) - self._RECOVERY_FEEDBACK_ENVELOPE_TOLERANCE_DEGREES
                    envelope_max = max(start, goal) + self._RECOVERY_FEEDBACK_ENVELOPE_TOLERANCE_DEGREES
                    if not envelope_min <= actual <= envelope_max:
                        feedback_warnings.append(
                            f"{joint}: feedback {actual:.2f} left verified envelope "
                            f"{envelope_min:.2f}..{envelope_max:.2f}"
                        )

                    previous_actual = _recovery_previous_feedback.get(joint, actual)
                    feedback_velocity = abs(actual - previous_actual) / feedback_elapsed
                    if feedback_velocity > self._RECOVERY_MAX_FEEDBACK_VELOCITY:
                        feedback_warnings.append(
                            f"{joint}: feedback velocity {feedback_velocity:.2f} deg/s exceeds "
                            f"{self._RECOVERY_MAX_FEEDBACK_VELOCITY:.2f} deg/s"
                        )

                    error = abs(actual - goal)
                    best_error = _recovery_best_error.get(joint, error)
                    if error <= self._RECOVERY_TARGET_TOLERANCE_DEGREES:
                        _recovery_best_error[joint] = error
                        _recovery_last_progress_at[joint] = now
                    elif best_error - error >= self._RECOVERY_PROGRESS_EPSILON_DEGREES:
                        _recovery_best_error[joint] = error
                        _recovery_last_progress_at[joint] = now
                    elif (
                        joint in _recovery_last_progress_at
                        and now - _recovery_last_progress_at[joint] >= self._RECOVERY_STALL_TIMEOUT_S
                    ):
                        stalled_joints.append(joint)

                _recovery_previous_feedback = dict(self._current_state.positions)
                _recovery_previous_feedback_at = now

                if stalled_joints:
                    feedback_warnings.append(
                        "no measurable progress for "
                        f"{self._RECOVERY_STALL_TIMEOUT_S:.1f}s: {', '.join(sorted(stalled_joints))}"
                    )

                if feedback_warnings and now - _recovery_last_warning_at >= 1.0:
                    # A software observation alone must not release torque.
                    # Recovery commands remain bounded and monotonic; if a
                    # loaded joint pauses, keep holding/commanding the verified
                    # target and let the configured motor torque/current limit
                    # provide the electrical protection layer.
                    logger.warning(
                        "motion.recovery_feedback_warning",
                        reason="; ".join(feedback_warnings),
                        torque_held=True,
                    )
                    _recovery_last_warning_at = now

            if self._safety.is_estopped():
                self._tick_sleep(t0)
                continue

            # --- Anticipation countdown ---
            # While _anticipation_ticks_remaining > 0, the spring holds the
            # windup (opposite-direction) target.  Once the countdown expires,
            # switch to the real target stored in _anticipation_final_target.
            if _anticipation_ticks_remaining > 0:
                _anticipation_ticks_remaining -= 1
                if _anticipation_ticks_remaining == 0 and _anticipation_final_target is not None:
                    self._current_target = _anticipation_final_target
                    _anticipation_final_target = None
                    logger.debug("motion.anticipation_done_switching_to_final")

            # --- Build per-joint targets ---
            # Start with each spring holding its own current position
            # (passive joints decelerate naturally rather than tracking noisy hw)
            joint_targets: dict[str, float] = {j: s.position for j, s in _springs.items()}

            if _stream_frames:
                # Advance accumulator and update frame target when due
                _stream_accumulator += dt
                frame_interval = 1.0 / _stream_fps
                while _stream_accumulator >= frame_interval and _stream_idx < len(_stream_frames):
                    _current_stream_target = dict(_stream_frames[_stream_idx])
                    _stream_idx += 1
                    _stream_accumulator -= frame_interval

                # Feed current frame target into spring (continuously, every tick)
                joint_targets.update(_current_stream_target)

                progress = _stream_idx / len(_stream_frames)
                self._status = MotionStatus(progress=progress, is_done=False)

                # All frames consumed → transition to settling phase
                if _stream_idx >= len(_stream_frames):
                    _stream_frames = []
                    _stream_settling = True
                    if self._recovery_target is not None:
                        # A loaded joint may legitimately lag behind the
                        # prevalidated command timeline. Keep commanding the
                        # verified endpoint until feedback actually reaches it;
                        # the recovery watchdog remains responsible for real
                        # stalls and unsafe feedback. A wall-clock timeout here
                        # used to release torque while the arm was still making
                        # steady progress, causing it to fall.
                        _stream_settle_timeout = 0.0
                        logger.info(
                            "motion.recovery_holding_target",
                            remaining={
                                joint: round(abs(self._current_state.get(joint, goal) - goal), 2)
                                for joint, goal in self._recovery_target.items()
                            },
                        )
                    else:
                        _stream_settle_timeout = time.monotonic() + self._STREAM_SETTLE_TIMEOUT_S

            elif _stream_settling:
                # All frames consumed; keep feeding last target until arm settles.
                # Use hardware position for done-detection (not spring internal state)
                # so large frame steps that get velocity-clamped still complete.
                joint_targets.update(_current_stream_target)
                # Recovery uses the same final tolerance as its progress
                # watchdog.  A joint already within that envelope must not
                # keep the whole arm settling until timeout or be called
                # stalled because of encoder quantization/static friction.
                hw_settle_tol = (
                    self._RECOVERY_TARGET_TOLERANCE_DEGREES if self._recovery_target is not None else 1.0
                )
                hw_at_target = all(
                    abs(self._current_state.get(j, v) - v) <= hw_settle_tol
                    for j, v in _current_stream_target.items()
                )
                is_recovery = self._recovery_target is not None
                timed_out = not is_recovery and time.monotonic() >= _stream_settle_timeout
                if hw_at_target or timed_out:
                    _stream_settling = False
                    self._status = MotionStatus(progress=1.0, is_done=True)
                    if is_recovery and hw_at_target:
                        logger.info(
                            "motion.recovery_target_reached",
                            actual={
                                joint: round(self._current_state.get(joint, goal), 2)
                                for joint, goal in self._recovery_target.items()
                            },
                        )
                    if _active_done:
                        _active_done.set()
                        _active_done = None

            elif self._current_target is not None:
                # Goal-based: spring tracks the fixed target position
                joint_targets.update(self._current_target.joints)

            # --- Apply spring filter to all joints ---
            next_frame: dict[str, float] = {}
            stream_active = bool(_stream_frames) or _stream_settling
            passthrough_joints = _current_stream_target.keys() if (stream_active and _stream_passthrough) else ()
            for joint, target_pos in joint_targets.items():
                if joint not in _springs:
                    _springs[joint] = SecondOrderDynamics(
                        _spring_f,
                        _spring_z,
                        initial=self._current_state.get(joint, target_pos),
                    )
                if joint in passthrough_joints:
                    _springs[joint].sync_position(target_pos)
                    next_frame[joint] = target_pos
                    continue
                next_frame[joint] = _springs[joint].update(target_pos, dt)

            # --- Overlapping Action (P2) ---
            overlap_enabled = _stream_enable_overlap if stream_active else self._config.overlapping_action
            if overlap_enabled and next_frame:
                next_frame = self._apply_overlapping_action(next_frame, _overlap_prev, _overlap_buffers)
            _overlap_prev = dict(next_frame)

            # --- Safety validate and write (unified path for all sources) ---
            clip_events: list[dict[str, float | str]] = []
            command_reference = dict(self._current_state.positions)
            command_reference.update(_last_safe_frame)
            if self._recovery_target is not None:
                safe_frame = self._safety.validate_recovery_frame(
                    self._current_state,
                    next_frame,
                    self._recovery_target,
                    dt,
                    max_velocity=self._recovery_max_velocity,
                    command_reference=_last_safe_frame,
                    max_command_lead=self._RECOVERY_COMMAND_LEAD_DEGREES,
                    clip_events=clip_events,
                )
            else:
                safe_frame = self._safety.validate_frame(
                    JointState(positions=command_reference, timestamp=self._current_state.timestamp),
                    next_frame,
                    dt,
                    clip_events=clip_events,
                )
            _last_safe_frame.update(safe_frame)
            if stream_active and clip_events:
                _stream_clip_events_in_window += len(clip_events)
                _stream_clipped_joints_in_window.update(str(event["joint"]) for event in clip_events)
            now = time.monotonic()
            window_elapsed = now - _stream_clip_window_start
            should_flush_stream_clip_log = window_elapsed >= 1.0 or (
                not stream_active and _stream_clip_events_in_window > 0
            )
            if should_flush_stream_clip_log:
                if self._recovery_target is not None:
                    logger.info(
                        "motion.recovery_tracking",
                        actual={
                            joint: round(self._current_state.get(joint, goal), 2)
                            for joint, goal in self._recovery_target.items()
                        },
                        commanded={
                            joint: round(_last_safe_frame.get(joint, goal), 2)
                            for joint, goal in self._recovery_target.items()
                        },
                        remaining={
                            joint: round(abs(self._current_state.get(joint, goal) - goal), 2)
                            for joint, goal in self._recovery_target.items()
                        },
                    )
                elif _stream_clip_events_in_window > 0:
                    logger.warning(
                        "motion.stream_velocity_clipped_summary",
                        window_seconds=round(window_elapsed, 3),
                        clipped_joint_count=len(_stream_clipped_joints_in_window),
                        clipped_joints=sorted(_stream_clipped_joints_in_window),
                        clip_event_count=_stream_clip_events_in_window,
                    )
                _stream_clip_window_start = now
                _stream_clipped_joints_in_window.clear()
                _stream_clip_events_in_window = 0

            # Sync spring positions when safety clamped significantly,
            # so the filter does not race ahead of the real arm.
            for joint, safe_pos in safe_frame.items():
                spring_pos = next_frame.get(joint, safe_pos)
                if joint in _springs and abs(safe_pos - spring_pos) > 0.5:
                    _springs[joint].sync_position(safe_pos)

            try:
                if self._recovery_target is not None:
                    self._hal.write_recovery_positions(safe_frame)
                else:
                    tick_ms = max(1, round(self._tick_interval * 1000))
                    self._hal.write_positions(safe_frame, move_time_ms=tick_ms)
            except Exception:
                self._safety.report_bus_health(False)
                logger.exception("motion.write_failed")

            # --- Diagnostics ---
            if _diag_mode:
                logger.info(
                    "motion.diag_tick",
                    targets={k: round(v, 2) for k, v in joint_targets.items()},
                    spring={k: round(v, 2) for k, v in next_frame.items()},
                    safe={k: round(v, 2) for k, v in safe_frame.items()},
                    hw={k: round(v, 2) for k, v in self._current_state.positions.items()},
                )

            # --- Done detection for MOVE_TO ---
            if self._current_target is not None and not _stream_frames:
                spring_settled = all(
                    _springs[j].is_settled(tv) for j, tv in self._current_target.joints.items() if j in _springs
                )
                hardware_at_target = all(
                    abs(self._current_state.get(j, tv) - tv) < 1.0 for j, tv in self._current_target.joints.items()
                )
                all_settled = spring_settled and hardware_at_target

                _was_stalled = False
                if not all_settled:
                    hw_remaining = sum(
                        abs(tv - self._current_state.get(j, tv)) for j, tv in self._current_target.joints.items()
                    )
                    if _prev_hw_remaining >= 0 and abs(hw_remaining - _prev_hw_remaining) < 0.3:
                        _stall_ticks += 1
                    else:
                        _stall_ticks = 0
                    _prev_hw_remaining = hw_remaining

                    if _stall_ticks > 250:
                        logger.warning(
                            "motion.move_stalled",
                            remaining={
                                j: round(self._current_state.get(j, tv) - tv, 1)
                                for j, tv in self._current_target.joints.items()
                            },
                        )
                        all_settled = True
                        _was_stalled = True

                if all_settled:
                    self._current_target = None
                    self._status = MotionStatus(progress=1.0, is_done=True, stalled=_was_stalled)
                    logger.info("motion.move_done")
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                else:
                    hw_remaining = sum(
                        abs(tv - self._current_state.get(j, tv)) for j, tv in self._current_target.joints.items()
                    )
                    progress = 1.0 - hw_remaining / _initial_distance if _initial_distance > 1.0 else 1.0
                    self._status = MotionStatus(
                        target=self._current_target,
                        progress=max(0.0, min(1.0, progress)),
                        is_done=False,
                    )

            _diag_counter += 1
            if _diag_counter % 250 == 0 and self._current_target is not None:
                logger.info(
                    "motion.diag",
                    pos={k: round(v, 1) for k, v in self._current_state.positions.items()},
                    target={k: round(v, 1) for k, v in self._current_target.joints.items()},
                    progress=round(self._status.progress, 3),
                    estopped=self._safety.is_estopped(),
                )

            self._tick_sleep(t0)

        logger.info("motion.control_loop.exit")

    # ------------------------------------------------------------------
    # Overlapping Action — secondary joint coupling
    # ------------------------------------------------------------------

    _OVERLAP_COUPLINGS: list[tuple[str, str, float, int]] = [
        # (primary_joint, secondary_joint, ratio, lag_ticks)
        # Ratios raised 3-4x from original (0.04/0.06/0.03) so the coupling
        # is actually visible; lag increased for more organic follow-through.
        # When the lamp pitches, yaw and elbow follow with a delay.
        ("base_pitch", "base_yaw", 0.12, 5),
        ("base_pitch", "elbow_pitch", 0.18, 6),
        # When the lamp yaws, wrist roll and elbow follow.
        ("base_yaw", "wrist_roll", 0.10, 4),
        ("base_yaw", "elbow_pitch", 0.08, 5),
        # When the elbow moves, the wrist pitch follows (energy transfer down the chain).
        ("elbow_pitch", "wrist_pitch", 0.15, 5),
    ]

    @staticmethod
    def _apply_overlapping_action(
        frame: dict[str, float],
        prev_frame: dict[str, float],
        buffers: dict[str, list[float]],
    ) -> dict[str, float]:
        """Add a delayed, scaled echo of primary joint displacement to
        secondary joints (Overlapping Action animation principle).

        The secondary joint's spring still tracks its own target —
        this offset is additive and temporary; the spring's restoring
        force naturally brings the secondary joint back.
        """
        result = dict(frame)
        for primary, secondary, ratio, lag in MotionRuntime._OVERLAP_COUPLINGS:
            if primary not in frame or secondary not in frame:
                continue
            delta = frame[primary] - prev_frame.get(primary, frame[primary])
            buf_key = f"{primary}→{secondary}"
            buf = buffers.setdefault(buf_key, [0.0] * lag)
            buf.append(delta)
            lagged_delta = buf.pop(0) if len(buf) > lag else 0.0
            result[secondary] = result[secondary] + lagged_delta * ratio
        return result

    # ------------------------------------------------------------------
    # Tick timing
    # ------------------------------------------------------------------

    def _tick_sleep(self, t0: float) -> None:
        elapsed = time.monotonic() - t0
        remaining = self._tick_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        elif remaining < -self._tick_interval * 0.5:
            logger.warning("motion.tick_overrun", elapsed_ms=elapsed * 1000)
