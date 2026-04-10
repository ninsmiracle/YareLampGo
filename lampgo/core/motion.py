"""MotionRuntime — trapezoidal-velocity trajectory tracking in a dedicated thread.

This is THE fix for the stuttering problem.  Instead of resetting linear
interpolation on every new target, the control loop runs a trapezoidal
velocity profile that smoothly accelerates, cruises, and decelerates.
New targets can be injected at any time without resetting velocity.
"""

from __future__ import annotations

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
from lampgo.core.style import TrajectoryPlan, get_motion_style, resolve_style_name
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

        # Per-joint velocity state for trapezoidal profile
        self._joint_velocities: dict[str, float] = {}
        self._planned_positions: dict[str, float] = {}
        self._trajectory_plan: TrajectoryPlan | None = None

        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API (called from asyncio side)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
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
        logger.info("motion.stopped")

    def update_target(self, target: MotionTarget) -> None:
        """Send a new target without resetting velocity — the key jitter fix."""
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

    def stream_frames(self, frames: list[dict[str, float]], fps: int = 30) -> threading.Event:
        """Queue frame-by-frame playback (CSV recordings). Returns done event."""
        done = threading.Event()
        cmd = _Command(type=_CommandType.STREAM_FRAMES, frames=frames, fps=fps, done_event=done)
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

    # ------------------------------------------------------------------
    # Control thread
    # ------------------------------------------------------------------

    def _control_loop(self) -> None:
        """Strict-tick control loop running in a dedicated thread."""
        logger.info("motion.control_loop.start")

        # Read initial state
        try:
            self._current_state = self._hal.read_positions()
            self._safety.report_bus_health(True)
        except Exception:
            logger.exception("motion.initial_read_failed")
            self._safety.report_bus_health(False)

        _active_done: threading.Event | None = None
        _stream_frames: list[dict[str, float]] = []
        _stream_idx = 0
        _stream_fps = 30
        _stream_accumulator = 0.0
        _diag_counter = 0
        _initial_distance = 0.0
        _stall_ticks = 0
        _prev_hw_remaining = -1.0
        # LAMPGO_DIAG=1 enables per-tick trajectory diagnostics (planned vs safe vs hw)
        _diag_mode: bool = os.environ.get("LAMPGO_DIAG", "0").strip() == "1"

        while self._running:
            t0 = time.monotonic()
            dt = self._tick_interval

            # --- Drain command queue (use latest MOVE_TO, process all others) ---
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
                    self._joint_velocities.clear()
                    self._planned_positions.clear()
                    self._trajectory_plan = None
                    _stream_frames = []
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                    self._status = MotionStatus(is_done=True)

                elif cmd.type == _CommandType.STOP_SMOOTH:
                    self._current_target = None
                    self._trajectory_plan = None
                    _stream_frames = []

                elif cmd.type == _CommandType.MOVE_TO:
                    validated = self._safety.validate_target(self._current_state, cmd.target)
                    if isinstance(validated, MotionTarget):
                        self._current_target = validated
                        self._planned_positions = dict(self._current_state.positions)
                        _stream_frames = []
                        style_key = resolve_style_name(validated.style, self._config.default_style)
                        if style_key == "linear":
                            self._trajectory_plan = None
                        else:
                            self._trajectory_plan = TrajectoryPlan.create(
                                self._current_state.positions,
                                validated.joints,
                                get_motion_style(style_key, self._config.default_style),
                                validated.max_velocity or self._config.default_max_velocity,
                                self._config.default_max_velocity,
                                safety_max_velocity=self._safety._config.max_velocity,
                            )
                            self._joint_velocities.clear()
                        if _active_done:
                            _active_done.set()
                        _active_done = cmd.done_event
                        _initial_distance = sum(
                            abs(v - self._current_state.get(k, v))
                            for k, v in validated.joints.items()
                        )
                        _stall_ticks = 0
                        _prev_hw_remaining = -1.0
                        self._status = MotionStatus(target=validated, progress=0.0, is_done=False)
                        logger.info(
                            "motion.move_accepted",
                            target=validated.joints,
                            vel=validated.max_velocity,
                            style=style_key,
                        )
                    else:
                        logger.warning("motion.target_rejected", reason=getattr(validated, "reason", ""))
                        if cmd.done_event:
                            cmd.done_event.set()

                elif cmd.type == _CommandType.STREAM_FRAMES:
                    _stream_frames = cmd.frames or []
                    _stream_idx = 0
                    _stream_accumulator = 0.0
                    _stream_fps = cmd.fps or 30
                    self._current_target = None
                    self._trajectory_plan = None
                    if _active_done:
                        _active_done.set()
                    _active_done = cmd.done_event
                    self._status = MotionStatus(progress=0.0, is_done=False)

            # --- Read current state ---
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

            # --- Compute next frame ---
            next_frame: dict[str, float] | None = None
            playback_frame_due = False

            if _stream_frames:
                _stream_accumulator += dt
                frame_interval = 1.0 / _stream_fps
                if _stream_accumulator >= frame_interval and _stream_idx < len(_stream_frames):
                    next_frame = _stream_frames[_stream_idx]
                    playback_frame_due = True
                    _stream_idx += 1
                    _stream_accumulator -= frame_interval
                    progress = _stream_idx / len(_stream_frames)
                    self._status = MotionStatus(progress=progress, is_done=False)

                if _stream_idx >= len(_stream_frames):
                    _stream_frames = []
                    self._status = MotionStatus(progress=1.0, is_done=True)
                    if _active_done:
                        _active_done.set()
                        _active_done = None

            elif self._current_target is not None:
                if self._trajectory_plan is not None:
                    framed, _plan_phase = self._trajectory_plan.sample(dt)
                    for k, v in framed.items():
                        self._planned_positions[k] = v
                    next_frame = framed
                else:
                    next_frame = self._trapezoidal_step(self._current_target, dt)

            # --- Validate and write ---
            safe_frame = None
            if next_frame is not None:
                if playback_frame_due:
                    safe_frame = self._safety.clamp_positions(self._current_state, next_frame)
                else:
                    safe_frame = self._safety.validate_frame(self._current_state, next_frame, dt)
                try:
                    self._hal.write_positions(safe_frame)
                except Exception:
                    self._safety.report_bus_health(False)
                    logger.exception("motion.write_failed")

                # --- Trajectory diagnostics (LAMPGO_DIAG=1) ---
                if _diag_mode and self._trajectory_plan is not None and next_frame is not None:
                    clamped_joints = {
                        j for j in next_frame
                        if safe_frame is not None and abs(safe_frame.get(j, next_frame[j]) - next_frame[j]) > 0.01
                    }
                    logger.info(
                        "motion.diag_traj",
                        elapsed=round(self._trajectory_plan.elapsed, 4),
                        planned={k: round(v, 2) for k, v in next_frame.items()},
                        safe={k: round(v, 2) for k, v in safe_frame.items()} if safe_frame else {},
                        hw={k: round(v, 2) for k, v in self._current_state.positions.items()},
                        velocity_clamped=sorted(clamped_joints),
                    )

            # --- Check move completion (after write) ---
            if self._current_target is not None and not _stream_frames:
                check_pos = safe_frame if safe_frame else self._current_state.positions
                hw_remaining = sum(
                    abs(tv - check_pos.get(j, self._current_state.get(j, tv)))
                    for j, tv in self._current_target.joints.items()
                )

                _was_stalled = False
                # Tight tolerance so styled trajectories + per-tick velocity clamps still reach goal
                # (1.0° was too loose: motion could "complete" before final commanded pose).
                _done_tol = 0.2
                all_done = all(
                    abs(check_pos.get(j, self._current_state.get(j, tv)) - tv) < _done_tol
                    for j, tv in self._current_target.joints.items()
                )

                if not all_done:
                    actual_remaining = sum(
                        abs(tv - self._current_state.get(j, tv))
                        for j, tv in self._current_target.joints.items()
                    )
                    if _prev_hw_remaining >= 0 and abs(actual_remaining - _prev_hw_remaining) < 0.3:
                        _stall_ticks += 1
                    else:
                        _stall_ticks = 0
                    _prev_hw_remaining = actual_remaining

                    if _stall_ticks > 250:
                        logger.warning("motion.move_stalled", remaining={
                            j: round(self._current_state.get(j, tv) - tv, 1)
                            for j, tv in self._current_target.joints.items()
                        })
                        all_done = True
                        _was_stalled = True

                if all_done:
                    self._current_target = None
                    self._joint_velocities.clear()
                    self._planned_positions.clear()
                    self._trajectory_plan = None
                    self._status = MotionStatus(progress=1.0, is_done=True, stalled=_was_stalled)
                    logger.info("motion.move_done")
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                else:
                    progress = 1.0 - (hw_remaining / _initial_distance) if _initial_distance > 1.0 else 1.0
                    self._status = MotionStatus(target=self._current_target, progress=max(0.0, min(1.0, progress)), is_done=False)

            _diag_counter += 1
            if _diag_counter % 250 == 0 and self._current_target is not None:
                logger.info(
                    "motion.diag",
                    pos={k: round(v, 1) for k, v in self._current_state.positions.items()},
                    target={k: round(v, 1) for k, v in self._current_target.joints.items()},
                    wrote=({k: round(v, 1) for k, v in safe_frame.items()} if safe_frame else None),
                    progress=round(self._status.progress, 3),
                    estopped=self._safety.is_estopped(),
                )

            self._tick_sleep(t0)

        logger.info("motion.control_loop.exit")

    # ------------------------------------------------------------------
    # Trapezoidal velocity profile
    # ------------------------------------------------------------------

    def _trapezoidal_step(self, target: MotionTarget, dt: float) -> dict[str, float]:
        """Compute the next frame for each joint using trapezoidal velocity.

        Uses planned positions (not hardware feedback) so the trajectory
        advances every tick regardless of servo response latency.
        Hardware feedback is still used by SafetyKernel.validate_frame().
        """
        max_vel = target.max_velocity or self._config.default_max_velocity
        max_acc = target.max_acceleration or self._config.default_max_acceleration
        result: dict[str, float] = {}

        for joint, target_pos in target.joints.items():
            current_pos = self._planned_positions.get(joint, self._current_state.get(joint, target_pos))
            current_vel = self._joint_velocities.get(joint, 0.0)

            error = target_pos - current_pos
            distance = abs(error)
            direction = 1.0 if error > 0 else (-1.0 if error < 0 else 0.0)

            stopping_dist = (current_vel**2) / (2.0 * max_acc) if max_acc > 0 else 0.0

            if distance < 0.1:
                result[joint] = target_pos
                self._joint_velocities[joint] = 0.0
                self._planned_positions[joint] = target_pos
                continue

            if stopping_dist >= distance:
                desired_vel = max(0.0, abs(current_vel) - max_acc * dt)
            else:
                desired_vel = min(max_vel, abs(current_vel) + max_acc * dt)

            desired_vel = min(desired_vel, max_vel)
            new_pos = current_pos + direction * desired_vel * dt
            self._joint_velocities[joint] = desired_vel
            self._planned_positions[joint] = new_pos

            result[joint] = new_pos

        return result

    def _tick_sleep(self, t0: float) -> None:
        elapsed = time.monotonic() - t0
        remaining = self._tick_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        elif remaining < -self._tick_interval * 0.5:
            logger.warning("motion.tick_overrun", elapsed_ms=elapsed * 1000)
