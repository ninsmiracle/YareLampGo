"""MotionRuntime — trapezoidal-velocity trajectory tracking in a dedicated thread.

This is THE fix for the stuttering problem.  Instead of resetting linear
interpolation on every new target, the control loop runs a trapezoidal
velocity profile that smoothly accelerates, cruises, and decelerates.
New targets can be injected at any time without resetting velocity.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto

import structlog

from lampgo.core.config import MotionConfig
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.safety import SafetyKernel
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
                    _stream_frames = []
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                    self._status = MotionStatus(is_done=True)

                elif cmd.type == _CommandType.STOP_SMOOTH:
                    self._current_target = None
                    _stream_frames = []

                elif cmd.type == _CommandType.MOVE_TO:
                    validated = self._safety.validate_target(self._current_state, cmd.target)
                    if isinstance(validated, MotionTarget):
                        self._current_target = validated
                        self._planned_positions = dict(self._current_state.positions)
                        _stream_frames = []
                        if _active_done:
                            _active_done.set()
                        _active_done = cmd.done_event
                        self._status = MotionStatus(target=validated, progress=0.0, is_done=False)
                        logger.info("motion.move_accepted", target=validated.joints, vel=validated.max_velocity)
                    else:
                        logger.warning("motion.target_rejected", reason=getattr(validated, 'reason', ''))
                        if cmd.done_event:
                            cmd.done_event.set()

                elif cmd.type == _CommandType.STREAM_FRAMES:
                    _stream_frames = cmd.frames or []
                    _stream_idx = 0
                    _stream_accumulator = 0.0
                    _stream_fps = cmd.fps or 30
                    self._current_target = None
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

            if _stream_frames:
                _stream_accumulator += dt
                frame_interval = 1.0 / _stream_fps
                if _stream_accumulator >= frame_interval and _stream_idx < len(_stream_frames):
                    next_frame = _stream_frames[_stream_idx]
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
                next_frame = self._trapezoidal_step(self._current_target, dt)

                all_done = True
                for joint, target_val in self._current_target.joints.items():
                    if next_frame and abs(next_frame.get(joint, target_val) - target_val) > 0.5:
                        all_done = False
                        break

                if all_done:
                    self._current_target = None
                    self._joint_velocities.clear()
                    self._planned_positions.clear()
                    self._status = MotionStatus(progress=1.0, is_done=True)
                    logger.info("motion.move_done")
                    if _active_done:
                        _active_done.set()
                        _active_done = None
                else:
                    total_dist = 0.0
                    remaining_dist = 0.0
                    for joint, target_val in self._current_target.joints.items():
                        cur = self._current_state.get(joint, target_val)
                        d = abs(target_val - cur)
                        total_dist += d
                        if next_frame:
                            remaining_dist += abs(target_val - next_frame.get(joint, target_val))
                    progress = 1.0 - (remaining_dist / total_dist) if total_dist > 0 else 1.0
                    self._status = MotionStatus(
                        target=self._current_target, progress=max(0.0, min(1.0, progress)), is_done=False
                    )

            # --- Validate and write ---
            if next_frame is not None:
                safe_frame = self._safety.validate_frame(self._current_state, next_frame, dt)
                try:
                    self._hal.write_positions(safe_frame)
                except Exception:
                    self._safety.report_bus_health(False)
                    logger.exception("motion.write_failed")

            _diag_counter += 1
            if _diag_counter % 250 == 0 and self._current_target is not None:
                logger.info(
                    "motion.diag",
                    pos={k: round(v, 1) for k, v in self._current_state.positions.items()},
                    target={k: round(v, 1) for k, v in self._current_target.joints.items()},
                    wrote=({k: round(v, 1) for k, v in safe_frame.items()} if next_frame else None),
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
            current_pos = self._planned_positions.get(
                joint, self._current_state.get(joint, target_pos)
            )
            current_vel = self._joint_velocities.get(joint, 0.0)

            error = target_pos - current_pos
            distance = abs(error)
            direction = 1.0 if error > 0 else (-1.0 if error < 0 else 0.0)

            stopping_dist = (current_vel ** 2) / (2.0 * max_acc) if max_acc > 0 else 0.0

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
