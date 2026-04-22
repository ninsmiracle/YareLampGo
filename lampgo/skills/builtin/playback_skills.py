"""Playback skill — play pre-recorded CSV action files.

Motion paradigm: TRAJECTORY-BASED (stream_frames).

CSV recordings are complete joint trajectories captured from human teleoperation.
They must be played back via ``stream_frames`` so the control thread executes each
frame at the original FPS with no trajectory replanning.  Using ``move_to``
waypoints would reset joint velocities ~10 times per second and destroy the
natural acceleration/deceleration in the recorded motion.

See docs/architecture.md § Motion Paradigms for the full paradigm guide.
"""

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
PLAYBACK_MODES = {"raw", "cleaned", "expressive"}
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

    Accepts two column naming conventions:
    - Native recorder format: ``base_yaw.pos``, ``base_pitch.pos``, ... + optional ``timestamp``
    - Simplified format (OpenClaw-generated): bare joint names ``base_yaw``, ``base_pitch``, ...
    """
    frames: list[dict[str, float]] = []
    timestamps: list[float] = []

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            frame: dict[str, float] = {}
            for joint in JOINT_NAMES:
                # Try native ".pos" suffix first, then bare joint name
                for key in (f"{joint}.pos", joint):
                    if key in row:
                        try:
                            frame[joint] = float(row[key])
                        except (ValueError, TypeError):
                            pass
                        break
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
        "playback_mode": ParameterSpec(
            name="playback_mode",
            type="str",
            required=False,
            default="cleaned",
            description="Playback mode: raw / cleaned / expressive",
        ),
    }

    _motion = None

    def __init__(self, recordings_dir: Path) -> None:
        self._recordings_dir = recordings_dir

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        name = params.get("name", "")
        if not name:
            return SkillResult(status="error", message="Recording name required")
        expression = str(params.get("expression", "")).strip()
        raw_mode = params.get("playback_mode")
        if raw_mode is None or not str(raw_mode).strip():
            # Fall back to the server-wide default configured via Web UI /
            # config.toml (motion.default_playback_mode). Tool-call invocations
            # that omit the parameter get the operator's preferred mode.
            playback_mode = getattr(getattr(ctx.motion, "_config", None), "default_playback_mode", "cleaned")
        else:
            playback_mode = str(raw_mode).strip().lower()
        if playback_mode not in PLAYBACK_MODES:
            logger.warning("playback.invalid_mode_fallback", requested=playback_mode, fallback="cleaned")
            playback_mode = "cleaned"

        # User-created recordings (user/) shadow built-ins of the same name.
        user_path = self._recordings_dir / "user" / f"{name}.csv"
        builtin_path = self._recordings_dir / f"{name}.csv"
        path = user_path if user_path.exists() else builtin_path
        if not path.exists():
            builtin = [p.stem for p in self._recordings_dir.glob("*.csv")]
            user_dir = self._recordings_dir / "user"
            user = [p.stem for p in user_dir.glob("*.csv")] if user_dir.is_dir() else []
            available = sorted(set(builtin + user))
            return SkillResult(
                status="error",
                message=f"Recording '{name}' not found. Available: {available}",
            )

        frames, detected_fps = load_recording(path)
        if not frames:
            return SkillResult(status="error", message=f"Recording '{name}' has no valid frames")

        fps = int(params.get("fps", 0)) or DEFAULT_RECORDING_FPS_OVERRIDES.get(name, detected_fps)
        logger.info("playback.start", name=name, frames=len(frames), fps=fps, playback_mode=playback_mode)

        if expression:
            if ctx.led.is_connected:
                if not ctx.led.set_mode(expression):
                    return SkillResult(status="error", message=f"Unknown expression: {expression}")
                logger.info("playback.expression_applied", name=name, expression=expression)
            else:
                logger.warning("playback.expression_skipped_led_disconnected", name=name, expression=expression)

        try:
            # Trajectory-based: stream the full frame sequence at original FPS.
            # The recorded human motion already contains natural acceleration/deceleration;
            # no style easing is applied on top.
            done = ctx.motion.stream_frames(frames, fps=fps, playback_mode=playback_mode)
            timeout = len(frames) / max(fps, 1) + 5.0
            if not await _await_done(done, timeout=timeout):
                logger.warning("playback.timeout", name=name, frames=len(frames), fps=fps)
                return SkillResult(status="error", message=f"Playback '{name}' timed out")
        finally:
            self._motion = None

        # Return to safe: goal-based (only the target pose is known).
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
                "playback_mode": playback_mode,
                "expression": expression or None,
                "returned_safe": True,
            },
        )

    async def cancel(self) -> None:
        if self._motion is not None:
            self._motion.stop_immediate()
