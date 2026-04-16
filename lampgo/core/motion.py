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
    idle          → joint_targets[all joints]   = BreathingGenerator output

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

import os
import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto

import structlog

from lampgo.core.breathing import BreathingGenerator
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
    done_event: threading.Event | None = None


class MotionRuntime:
    """Runs a dedicated control thread at a fixed tick rate.

    The asyncio side communicates via a thread-safe command queue.
    The control thread never blocks on asyncio.
    """

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

    # ------------------------------------------------------------------
    # Public API (called from asyncio / skill side)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._control_loop, name="lampgo-motion", daemon=True
        )
        self._thread.start()
        logger.info("motion.started", tick_hz=self._config.tick_rate_hz)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._command_queue.put(_Command(type=_CommandType.SHUTDOWN))
        if self._thread is not None:
            self._thread.join(timeout=2.0)
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

        # --- Breathing generator ---
        _breathing = BreathingGenerator(
            amplitude=self._config.breathing_amplitude
        )
        _breathing.set_rest(dict(self._current_state.positions))

        # --- Overlapping Action state ---
        _overlap_prev: dict[str, float] = dict(self._current_state.positions)
        # Circular buffers: key = "primary→secondary", value = list of deltas
        _overlap_buffers: dict[str, list[float]] = {}

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
                    _breathing.set_rest(
                        {j: s.position for j, s in _springs.items()}
                    )

                elif cmd.type == _CommandType.STOP_SMOOTH:
                    self._current_target = None
                    _stream_frames = []
                    _current_stream_target = {}
                    _stream_passthrough = False
                    _stream_enable_overlap = self._config.overlapping_action
                    _anticipation_final_target = None
                    _anticipation_ticks_remaining = 0
                    # Springs decelerate naturally — no hard stop
                    _breathing.set_rest(
                        {j: s.position for j, s in _springs.items()}
                    )

                elif cmd.type == _CommandType.MOVE_TO:
                    validated = self._safety.validate_target(
                        self._current_state, cmd.target
                    )
                    if isinstance(validated, MotionTarget):
                        _stream_frames = []
                        _current_stream_target = {}

                        # Resolve style → spring params
                        style_key = resolve_style_name(
                            validated.style, self._config.default_style
                        )
                        style = get_motion_style(
                            style_key, self._config.default_style
                        )

                        # Cap f to stay within velocity budget; preserve z
                        v_limit = (
                            validated.max_velocity or self._config.default_max_velocity
                        ) * 0.9
                        max_dist = max(
                            (
                                abs(v - self._current_state.get(k, v))
                                for k, v in validated.joints.items()
                            ),
                            default=0.0,
                        )
                        effective_f = cap_spring_f(
                            style.f, style.z, max_dist, v_limit
                        )
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
                            abs(v - self._current_state.get(k, v))
                            for k, v in validated.joints.items()
                        )
                        _stall_ticks = 0
                        _prev_hw_remaining = -1.0

                        # --- Anticipation: windup in the opposite direction ---
                        # Only for large moves and when not already in motion.
                        ant_cfg = self._config
                        if (
                            ant_cfg.anticipation_enabled
                            and max_dist >= ant_cfg.anticipation_threshold
                            and all(
                                abs(_springs[j].velocity) < 5.0
                                for j in validated.joints
                                if j in _springs
                            )
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
                            )
                            windup_validated = self._safety.validate_target(
                                self._current_state, windup_target
                            )
                            if isinstance(windup_validated, MotionTarget):
                                _anticipation_final_target = validated
                                _anticipation_ticks_remaining = max(
                                    1,
                                    round(
                                        ant_cfg.anticipation_duration_ms
                                        / 1000.0
                                        / self._tick_interval
                                    ),
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

                        self._status = MotionStatus(
                            target=validated, progress=0.0, is_done=False
                        )
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
                    _stream_frames = cmd.frames or []
                    _stream_idx = 0
                    _stream_accumulator = 0.0
                    _stream_fps = cmd.fps or 30
                    cmd_mode = cmd.playback_mode or "cleaned"
                    _stream_passthrough = cmd_mode == "raw"
                    _stream_enable_overlap = (
                        self._config.overlapping_action and cmd_mode == "expressive"
                    )
                    _current_stream_target = (
                        dict(_stream_frames[0]) if _stream_frames else {}
                    )
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

            # --- Read hardware state ---
            try:
                self._current_state = self._hal.read_positions()
                self._safety.report_bus_health(True)
            except Exception:
                self._safety.report_bus_health(False)
                self._tick_sleep(t0)
                continue

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
            joint_targets: dict[str, float] = {
                j: s.position for j, s in _springs.items()
            }

            if _stream_frames:
                # Advance accumulator and update frame target when due
                _stream_accumulator += dt
                frame_interval = 1.0 / _stream_fps
                while (
                    _stream_accumulator >= frame_interval
                    and _stream_idx < len(_stream_frames)
                ):
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
                    # Give the spring at most 2 s to settle before forcing done
                    _stream_settle_timeout = time.monotonic() + 2.0

            elif _stream_settling:
                # All frames consumed; keep feeding last target until arm settles.
                # Use hardware position for done-detection (not spring internal state)
                # so large frame steps that get velocity-clamped still complete.
                joint_targets.update(_current_stream_target)
                hw_settle_tol = 1.0   # degrees — arm close enough to last frame
                hw_vel_tol = 5.0      # deg/s   — based on tick delta
                hw_at_target = all(
                    abs(self._current_state.get(j, v) - v) < hw_settle_tol
                    for j, v in _current_stream_target.items()
                )
                timed_out = time.monotonic() >= _stream_settle_timeout
                if hw_at_target or timed_out:
                    _stream_settling = False
                    self._status = MotionStatus(progress=1.0, is_done=True)
                    _breathing.set_rest(
                        {j: s.position for j, s in _springs.items()}
                    )
                    if _active_done:
                        _active_done.set()
                        _active_done = None

            elif self._current_target is not None:
                # Goal-based: spring tracks the fixed target position
                joint_targets.update(self._current_target.joints)

            else:
                # Idle: breathing generator provides slow oscillating targets
                if self._config.breathing_enabled:
                    joint_targets.update(_breathing.sample(dt))

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
                next_frame = self._apply_overlapping_action(
                    next_frame, _overlap_prev, _overlap_buffers
                )
            _overlap_prev = dict(next_frame)

            # --- Safety validate and write (unified path for all sources) ---
            clip_events: list[dict[str, float | str]] = []
            safe_frame = self._safety.validate_frame(
                self._current_state, next_frame, dt, clip_events=clip_events
            )
            if stream_active and clip_events:
                _stream_clip_events_in_window += len(clip_events)
                _stream_clipped_joints_in_window.update(
                    str(event["joint"]) for event in clip_events
                )
            now = time.monotonic()
            window_elapsed = now - _stream_clip_window_start
            should_flush_stream_clip_log = (
                window_elapsed >= 1.0
                or (not stream_active and _stream_clip_events_in_window > 0)
            )
            if should_flush_stream_clip_log:
                if _stream_clip_events_in_window > 0:
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
                # Primary: spring settled at target for all commanded joints
                all_settled = all(
                    _springs[j].is_settled(tv)
                    for j, tv in self._current_target.joints.items()
                    if j in _springs
                )

                _was_stalled = False
                if not all_settled:
                    hw_remaining = sum(
                        abs(tv - self._current_state.get(j, tv))
                        for j, tv in self._current_target.joints.items()
                    )
                    if (
                        _prev_hw_remaining >= 0
                        and abs(hw_remaining - _prev_hw_remaining) < 0.3
                    ):
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
                    self._status = MotionStatus(
                        progress=1.0, is_done=True, stalled=_was_stalled
                    )
                    _breathing.set_rest(
                        {j: s.position for j, s in _springs.items()}
                    )
                    logger.info("motion.move_done")
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                else:
                    hw_remaining = sum(
                        abs(tv - self._current_state.get(j, tv))
                        for j, tv in self._current_target.joints.items()
                    )
                    progress = (
                        1.0 - hw_remaining / _initial_distance
                        if _initial_distance > 1.0
                        else 1.0
                    )
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
        ("base_pitch",  "base_yaw",    0.12, 5),
        ("base_pitch",  "elbow_pitch", 0.18, 6),
        # When the lamp yaws, wrist roll and elbow follow.
        ("base_yaw",    "wrist_roll",  0.10, 4),
        ("base_yaw",    "elbow_pitch", 0.08, 5),
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
