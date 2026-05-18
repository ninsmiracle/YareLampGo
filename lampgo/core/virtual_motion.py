"""In-memory motion runtime for ``--no-hw`` pet simulation.

This module intentionally never touches HAL or serial devices.  It mirrors the
small MotionRuntime surface used by skills so no-hardware sessions can still
produce joint poses for the Web pet.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto

import structlog

from lampgo.core.config import DEFAULT_JOINT_LIMITS, MotionConfig
from lampgo.core.types import JOINT_NAMES, JointState, MotionStatus, MotionTarget

logger = structlog.get_logger(__name__)


class _CommandType(Enum):
    MOVE_TO = auto()
    STREAM_FRAMES = auto()
    STOP = auto()
    SHUTDOWN = auto()


@dataclass
class _Command:
    type: _CommandType
    target: MotionTarget | None = None
    frames: list[dict[str, float]] | None = None
    fps: int = 30
    done_event: threading.Event | None = None


class VirtualMotionRuntime:
    """A non-hardware motion runtime that updates joint state in memory."""

    is_virtual = True

    def __init__(self, config: MotionConfig) -> None:
        self._config = config
        self._tick_interval = 1.0 / max(float(config.tick_rate_hz), 1.0)
        self._command_queue: queue.Queue[_Command] = queue.Queue(maxsize=64)
        self._state_lock = threading.Lock()
        self._current_state = JointState(positions={joint: 0.0 for joint in JOINT_NAMES})
        self._status = MotionStatus()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._command_queue = queue.Queue(maxsize=64)
        self._running = True
        self._thread = threading.Thread(
            target=self._control_loop,
            name="lampgo-virtual-motion",
            daemon=True,
        )
        self._thread.start()
        logger.info("virtual_motion.started", tick_hz=self._config.tick_rate_hz)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._command_queue.put(_Command(type=_CommandType.SHUTDOWN))
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("virtual_motion.stopped")

    def update_target(self, target: MotionTarget) -> None:
        cmd = _Command(type=_CommandType.MOVE_TO, target=target)
        try:
            self._command_queue.put_nowait(cmd)
        except queue.Full:
            try:
                self._command_queue.get_nowait()
            except queue.Empty:
                pass
            self._command_queue.put_nowait(cmd)

    def move_to(self, target: MotionTarget) -> threading.Event:
        done = threading.Event()
        self._command_queue.put(_Command(type=_CommandType.MOVE_TO, target=target, done_event=done))
        return done

    def stream_frames(
        self,
        frames: list[dict[str, float]],
        fps: int = 30,
        playback_mode: str = "cleaned",
    ) -> threading.Event:
        del playback_mode
        done = threading.Event()
        self._command_queue.put(
            _Command(type=_CommandType.STREAM_FRAMES, frames=frames, fps=fps, done_event=done)
        )
        return done

    def stop_smooth(self) -> None:
        self._command_queue.put(_Command(type=_CommandType.STOP))

    def stop_immediate(self) -> None:
        self._command_queue.put(_Command(type=_CommandType.STOP))

    @property
    def status(self) -> MotionStatus:
        return self._status

    @property
    def current_state(self) -> JointState:
        with self._state_lock:
            return JointState(positions=dict(self._current_state.positions))

    @property
    def is_running(self) -> bool:
        return self._running

    def _control_loop(self) -> None:
        active: _Command | None = None
        move_start: dict[str, float] = {}
        move_goal: dict[str, float] = {}
        move_started_at = 0.0
        move_duration = 0.0
        frame_index = 0
        next_frame_at = 0.0

        while self._running:
            now = time.monotonic()
            try:
                cmd = self._command_queue.get_nowait()
            except queue.Empty:
                cmd = None

            if cmd is not None:
                if cmd.type is _CommandType.SHUTDOWN:
                    if cmd.done_event:
                        cmd.done_event.set()
                    break
                if cmd.type is _CommandType.STOP:
                    active = None
                    self._status = MotionStatus(is_done=True)
                    if cmd.done_event:
                        cmd.done_event.set()
                elif cmd.type is _CommandType.MOVE_TO and cmd.target is not None:
                    active = cmd
                    current = self.current_state.positions
                    move_start = current
                    move_goal = self._merge_and_clamp(current, cmd.target.joints)
                    max_delta = max(
                        (abs(move_goal[j] - move_start.get(j, 0.0)) for j in move_goal),
                        default=0.0,
                    )
                    velocity = cmd.target.max_velocity or self._config.default_max_velocity
                    move_duration = max(0.08, max_delta / max(float(velocity), 1.0))
                    move_started_at = now
                    self._status = MotionStatus(target=cmd.target, progress=0.0, is_done=False)
                elif cmd.type is _CommandType.STREAM_FRAMES:
                    active = cmd
                    frame_index = 0
                    next_frame_at = now
                    self._status = MotionStatus(progress=0.0, is_done=False)

            if active is not None:
                if active.type is _CommandType.MOVE_TO and active.target is not None:
                    elapsed = max(0.0, now - move_started_at)
                    t = min(1.0, elapsed / max(move_duration, 0.001))
                    eased = _smoothstep(t)
                    pose = {
                        joint: move_start.get(joint, 0.0)
                        + (move_goal[joint] - move_start.get(joint, 0.0)) * eased
                        for joint in move_goal
                    }
                    self._set_state(pose)
                    self._status = MotionStatus(target=active.target, progress=t, is_done=t >= 1.0)
                    if t >= 1.0:
                        if active.done_event:
                            active.done_event.set()
                        active = None
                elif active.type is _CommandType.STREAM_FRAMES:
                    frames = active.frames or []
                    fps = max(int(active.fps or 30), 1)
                    if not frames:
                        if active.done_event:
                            active.done_event.set()
                        self._status = MotionStatus(progress=1.0, is_done=True)
                        active = None
                    elif now >= next_frame_at:
                        frame = frames[min(frame_index, len(frames) - 1)]
                        current = self.current_state.positions
                        self._set_state(self._merge_and_clamp(current, frame))
                        frame_index += 1
                        self._status = MotionStatus(
                            progress=min(1.0, frame_index / max(len(frames), 1)),
                            is_done=frame_index >= len(frames),
                        )
                        next_frame_at += 1.0 / fps
                        if frame_index >= len(frames):
                            if active.done_event:
                                active.done_event.set()
                            active = None

            time.sleep(self._tick_interval)

        self._running = False
        logger.info("virtual_motion.control_loop.stopped")

    def _set_state(self, positions: dict[str, float]) -> None:
        with self._state_lock:
            merged = dict(self._current_state.positions)
            merged.update(positions)
            self._current_state = JointState(positions=merged)

    @staticmethod
    def _merge_and_clamp(current: dict[str, float], updates: dict[str, float]) -> dict[str, float]:
        merged = dict(current)
        for joint, value in updates.items():
            if joint not in JOINT_NAMES:
                continue
            limit = DEFAULT_JOINT_LIMITS.get(joint)
            val = float(value)
            if limit is not None:
                val = max(limit.min, min(limit.max, val))
            merged[joint] = val
        return merged


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)
