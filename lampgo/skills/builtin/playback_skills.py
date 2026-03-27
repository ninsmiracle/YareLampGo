"""Playback skill — play pre-recorded CSV action files."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

import structlog

from lampgo.core.types import JOINT_NAMES, MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext
from lampgo.skills.builtin.motion_skills import get_safe_position

logger = structlog.get_logger(__name__)

RETURN_SAFE_TIMEOUT_S = 60.0
DEFAULT_RECORDING_FPS_OVERRIDES = {
    "celebrate": 9,
}


async def _await_done(done_event, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not done_event.is_set():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


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
            fps = max(1, int(round((len(timestamps) - 1) / total_time)))

    return frames, fps


class PlayRecordingSkill(Skill):
    skill_id = "play_recording"
    description = "Play a pre-recorded CSV action file."
    parameters = {
        "name": ParameterSpec(name="name", type="str", description="Recording name (without .csv)"),
        "fps": ParameterSpec(name="fps", type="int", required=False, description="Override playback fps"),
        "expression": ParameterSpec(
            name="expression",
            type="str",
            required=False,
            description="Optional LED expression to apply before playback",
        ),
    }

    def __init__(self, recordings_dir: Path) -> None:
        self._recordings_dir = recordings_dir

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        name = params.get("name", "")
        if not name:
            return SkillResult(status="error", message="Recording name required")
        expression = str(params.get("expression", "")).strip()

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

        fps = int(params.get("fps", 0)) or DEFAULT_RECORDING_FPS_OVERRIDES.get(name, detected_fps)
        logger.info("playback.start", name=name, frames=len(frames), fps=fps)

        if expression:
            if ctx.led.is_connected:
                if not ctx.led.set_mode(expression):
                    return SkillResult(status="error", message=f"Unknown expression: {expression}")
                logger.info("playback.expression_applied", name=name, expression=expression)
            else:
                logger.warning("playback.expression_skipped_led_disconnected", name=name, expression=expression)

        done_event = ctx.motion.stream_frames(frames, fps=fps)
        if not await _await_done(done_event, timeout=max(30.0, len(frames) / max(fps, 1) + 5.0)):
            logger.warning("playback.timeout", name=name)
            return SkillResult(status="error", message=f"Playback '{name}' did not complete within timeout")

        safe = get_safe_position()
        logger.info("playback.return_safe_start", name=name, target=safe)
        return_done = ctx.motion.move_to(MotionTarget(joints=dict(safe), max_velocity=60.0))
        if not await _await_done(return_done, timeout=RETURN_SAFE_TIMEOUT_S):
            logger.warning("playback.return_safe_timeout", name=name, target=safe)
            return SkillResult(status="error", message=f"Playback '{name}' finished but return_safe timed out")
        logger.info("playback.return_safe_done", name=name)

        return SkillResult(
            status="ok",
            data={
                "name": name,
                "frames": len(frames),
                "fps": fps,
                "expression": expression or None,
                "returned_safe": True,
            },
        )
