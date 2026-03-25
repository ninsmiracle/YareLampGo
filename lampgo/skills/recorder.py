"""TeachRecorder — record user manipulation as CSV for replay.

The user physically moves the arm (with torque off), and the recorder
captures joint positions at a fixed FPS. The resulting CSV can be played
back as a skill via PlayRecordingSkill.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import structlog

from lampgo.core.hal import HardwareAbstraction
from lampgo.core.types import JOINT_NAMES

logger = structlog.get_logger(__name__)


class TeachRecorder:
    """Records a teach session to a CSV file."""

    def __init__(self, hal: HardwareAbstraction, recordings_dir: Path, fps: int = 30) -> None:
        self._hal = hal
        self._recordings_dir = recordings_dir
        self._fps = fps
        self._frames: list[dict[str, float]] = []
        self._recording = False

    def start(self) -> None:
        """Begin recording. Torque should be disabled before calling this."""
        self._frames = []
        self._recording = True
        logger.info("recorder.started", fps=self._fps)

    def stop(self) -> None:
        self._recording = False
        logger.info("recorder.stopped", frames=len(self._frames))

    def tick(self) -> None:
        """Call at FPS rate to capture one frame."""
        if not self._recording:
            return
        state = self._hal.read_positions()
        frame = {"timestamp": time.monotonic()}
        frame.update(state.positions)
        self._frames.append(frame)

    def save(self, name: str) -> Path:
        """Save recorded frames to CSV and return the file path."""
        self._recordings_dir.mkdir(parents=True, exist_ok=True)
        path = self._recordings_dir / f"{name}.csv"

        fieldnames = ["timestamp"] + [f"{j}.pos" for j in JOINT_NAMES]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for frame in self._frames:
                row = {"timestamp": frame.get("timestamp", 0.0)}
                for joint in JOINT_NAMES:
                    row[f"{joint}.pos"] = frame.get(joint, 0.0)
                writer.writerow(row)

        logger.info("recorder.saved", name=name, path=str(path), frames=len(self._frames))
        return path

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def is_recording(self) -> bool:
        return self._recording


def smooth_trajectory(frames: list[dict[str, float]], window: int = 5) -> list[dict[str, float]]:
    """Apply a simple moving-average filter to smooth a recorded trajectory.

    This reduces noise from hand tremor during teach recording.
    """
    if len(frames) <= window:
        return frames

    smoothed: list[dict[str, float]] = []
    joints = [k for k in frames[0] if k != "timestamp"]

    for i in range(len(frames)):
        start = max(0, i - window // 2)
        end = min(len(frames), i + window // 2 + 1)
        window_frames = frames[start:end]

        frame: dict[str, float] = {}
        if "timestamp" in frames[i]:
            frame["timestamp"] = frames[i]["timestamp"]

        for joint in joints:
            values = [f[joint] for f in window_frames if joint in f]
            frame[joint] = sum(values) / len(values) if values else frames[i].get(joint, 0.0)

        smoothed.append(frame)

    return smoothed


def compress_trajectory(frames: list[dict[str, float]], threshold: float = 0.5) -> list[dict[str, float]]:
    """Remove frames where no joint moved more than threshold degrees.

    Reduces file size while keeping the motion shape intact.
    """
    if not frames:
        return frames

    result = [frames[0]]
    joints = [k for k in frames[0] if k != "timestamp"]

    for frame in frames[1:]:
        prev = result[-1]
        max_delta = max(abs(frame.get(j, 0) - prev.get(j, 0)) for j in joints)
        if max_delta >= threshold:
            result.append(frame)

    if result[-1] is not frames[-1]:
        result.append(frames[-1])

    return result
