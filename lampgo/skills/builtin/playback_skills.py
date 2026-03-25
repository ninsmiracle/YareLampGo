"""Playback skill — play pre-recorded CSV action files."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

import structlog

from lampgo.core.types import JOINT_NAMES, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)


def load_recording(path: Path) -> tuple[list[dict[str, float]], int]:
    """Load a CSV recording. Returns (frames, estimated_fps).

    CSV format: columns like ``base_yaw.pos``, ``base_pitch.pos``, ...
    plus an optional ``timestamp`` column.
    """
    frames: list[dict[str, float]] = []
    timestamps: list[float] = []

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            frame: dict[str, float] = {}
            for joint in JOINT_NAMES:
                key = f"{joint}.pos"
                if key in row:
                    try:
                        frame[joint] = float(row[key])
                    except (ValueError, TypeError):
                        pass
            if frame:
                frames.append(frame)
            if "timestamp" in row:
                try:
                    timestamps.append(float(row["timestamp"]))
                except (ValueError, TypeError):
                    pass

    fps = 30
    if len(timestamps) >= 2:
        total_time = timestamps[-1] - timestamps[0]
        if total_time > 0:
            fps = max(1, int(round(len(timestamps) / total_time)))

    return frames, fps


class PlayRecordingSkill(Skill):
    skill_id = "play_recording"
    description = "Play a pre-recorded CSV action file."
    parameters = {
        "name": ParameterSpec(name="name", type="str", description="Recording name (without .csv)"),
        "fps": ParameterSpec(name="fps", type="int", required=False, description="Override playback fps"),
    }

    def __init__(self, recordings_dir: Path) -> None:
        self._recordings_dir = recordings_dir

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        name = params.get("name", "")
        if not name:
            return SkillResult(status="error", message="Recording name required")

        path = self._recordings_dir / f"{name}.csv"
        if not path.exists():
            available = [p.stem for p in self._recordings_dir.glob("*.csv")]
            return SkillResult(
                status="error",
                message=f"Recording '{name}' not found. Available: {available}",
            )

        frames, detected_fps = load_recording(path)
        if not frames:
            return SkillResult(status="error", message=f"Recording '{name}' has no valid frames")

        fps = int(params.get("fps", 0)) or detected_fps
        logger.info("playback.start", name=name, frames=len(frames), fps=fps)

        done_event = ctx.motion.stream_frames(frames, fps=fps)
        while not done_event.is_set():
            await asyncio.sleep(0.05)

        return SkillResult(status="ok", data={"name": name, "frames": len(frames), "fps": fps})
