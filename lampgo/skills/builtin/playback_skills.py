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
MAX_WAYPOINT_RATE_HZ = 10
MIN_SEGMENT_TIMEOUT_S = 2.0
FIRST_SEGMENT_MIN_TIMEOUT_S = 6.0
SEGMENT_TIMEOUT_MARGIN_S = 0.8
SEGMENT_TIMEOUT_SCALE = 2.5


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


def _sample_waypoints(frames: list[dict[str, float]], fps: int, max_rate_hz: int = MAX_WAYPOINT_RATE_HZ) -> list[dict[str, float]]:
    """Downsample raw recording frames into move_to waypoints.

    Replaying every CSV row through move_to would trigger excessive micro-replans.
    We sample to a bounded keyframe rate and let MotionRuntime interpolate each
    segment using its planned motion path (style / linear profile).
    """
    if not frames:
        return []
    stride = max(1, int(round(max(fps, 1) / max(max_rate_hz, 1))))
    sampled = [frames[i] for i in range(0, len(frames), stride)]
    if sampled[-1] is not frames[-1]:
        sampled.append(frames[-1])
    return sampled


def _segment_timeout_s(
    start: dict[str, float],
    end: dict[str, float],
    velocity: float,
    *,
    is_first_segment: bool,
) -> float:
    """Estimate a safe timeout for one ``move_to`` segment.

    ``move_to`` drives joints concurrently, so we use the maximum joint delta as
    the dominant time term and add conservative buffer for style easing.
    """
    max_delta = max((abs(end.get(j, start.get(j, 0.0)) - start.get(j, 0.0)) for j in JOINT_NAMES), default=0.0)
    effective_velocity = max(abs(float(velocity)), 1.0)
    estimated_motion_s = max_delta / effective_velocity
    timeout_s = estimated_motion_s * SEGMENT_TIMEOUT_SCALE + SEGMENT_TIMEOUT_MARGIN_S
    if is_first_segment:
        # First segment often starts far from the recording's first waypoint.
        timeout_s = max(timeout_s, FIRST_SEGMENT_MIN_TIMEOUT_S)
    return max(timeout_s, MIN_SEGMENT_TIMEOUT_S)


class PlayRecordingSkill(Skill):
    skill_id = "play_recording"
    description = "Play a pre-recorded CSV action file."
    parameters = {
        "name": ParameterSpec(name="name", type="str", description="Recording name (without .csv)"),
        "fps": ParameterSpec(name="fps", type="int", required=False, description="Override playback fps"),
        "style": ParameterSpec(name="style", type="str", required=False, description="Playback move style"),
        "velocity": ParameterSpec(name="velocity", type="float", required=False, description="Playback max velocity"),
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
        style = str(params.get("style", "gentle") or "gentle")
        velocity_raw = params.get("velocity")
        velocity = float(velocity_raw) if velocity_raw is not None else 80.0
        logger.info("playback.start", name=name, frames=len(frames), fps=fps, style=style, velocity=velocity)

        if expression:
            if ctx.led.is_connected:
                if not ctx.led.set_mode(expression):
                    return SkillResult(status="error", message=f"Unknown expression: {expression}")
                logger.info("playback.expression_applied", name=name, expression=expression)
            else:
                logger.warning("playback.expression_skipped_led_disconnected", name=name, expression=expression)

        waypoints = _sample_waypoints(frames, fps=fps)
        start_pose = dict(ctx.motion.current_state.positions)
        for idx, joints in enumerate(waypoints):
            seg_timeout = _segment_timeout_s(
                start_pose,
                joints,
                velocity=velocity,
                is_first_segment=(idx == 0),
            )
            done_event = ctx.motion.move_to(MotionTarget(joints=joints, max_velocity=velocity, style=style))
            if not await _await_done(done_event, timeout=seg_timeout):
                logger.warning(
                    "playback.timeout",
                    name=name,
                    segment=idx,
                    segments=len(waypoints),
                    timeout_s=round(seg_timeout, 2),
                )
                return SkillResult(status="error", message=f"Playback '{name}' timed out on segment {idx + 1}")
            start_pose = joints

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
                "style": style,
                "safety_path": "validate_frame",
            },
        )
