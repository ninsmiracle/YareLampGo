"""Local vision cat teaser skill."""

from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import structlog

from lampgo.core.config import DEFAULT_JOINT_LIMITS
from lampgo.core.types import MotionTarget, SkillResult
from lampgo.perception.cat_teaser import (
    CatPlayObservation,
    CatPlayState,
    CatPlayStateEstimator,
    CatTeaserCameraError,
    CatTeaserDebugView,
    CatTeaserDependencyError,
    CatTeaserFrameSource,
    CatToyTracker,
)
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)

FrameSourceFactory = Callable[[], CatTeaserFrameSource]


class CatTeaserSkill(Skill):
    """Play with a cat using a colored-marker teaser wand and local vision."""

    skill_id = "cat_teaser"
    label = "逗猫棒互动"
    description = (
        "Track a colored marker on a cat teaser wand, estimate cat engagement "
        "from local motion, and drive playful bounded lamp-head motion."
    )
    parameters = {
        "duration": ParameterSpec(
            name="duration",
            type="float",
            required=False,
            default=60.0,
            description="Seconds to play; default 60.",
        ),
        "marker_color": ParameterSpec(
            name="marker_color",
            type="str",
            required=False,
            default="magenta",
            description="Colored marker on wand tip: magenta, green, blue, red, or yellow.",
        ),
        "energy": ParameterSpec(
            name="energy",
            type="str",
            required=False,
            default="normal",
            description="Motion intensity: gentle, normal, or active.",
        ),
        "camera_fps": ParameterSpec(
            name="camera_fps",
            type="float",
            required=False,
            default=8.0,
            description="Camera processing FPS, clamped to 2-12.",
        ),
        "max_yaw": ParameterSpec(
            name="max_yaw",
            type="float",
            required=False,
            default=25.0,
            description="Maximum yaw offset around the starting pose.",
        ),
        "max_pitch": ParameterSpec(
            name="max_pitch",
            type="float",
            required=False,
            default=14.0,
            description="Maximum base-pitch offset around the starting pose.",
        ),
        "max_wrist_pitch": ParameterSpec(
            name="max_wrist_pitch",
            type="float",
            required=False,
            default=8.0,
            description="Maximum wrist-pitch offset around the starting pose.",
        ),
        "debug_view": ParameterSpec(
            name="debug_view",
            type="bool",
            required=False,
            default=True,
            description="Show an OpenCV preview window with marker and state overlays.",
        ),
        "log_events": ParameterSpec(
            name="log_events",
            type="bool",
            required=False,
            default=True,
            description="Print Chinese interaction events such as touch and pounce detections.",
        ),
    }

    def __init__(self, frame_source_factory: FrameSourceFactory) -> None:
        self._frame_source_factory = frame_source_factory
        self._cancel_event: asyncio.Event | None = None
        self._motion = None
        self._source: CatTeaserFrameSource | None = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        try:
            duration = max(1.0, min(300.0, float(params.get("duration", 60.0))))
            marker_color = str(params.get("marker_color") or "magenta").strip().lower()
            energy = str(params.get("energy") or "normal").strip().lower()
            camera_fps = max(2.0, min(12.0, float(params.get("camera_fps", 8.0))))
            max_yaw = max(1.0, min(45.0, abs(float(params.get("max_yaw", 25.0)))))
            max_pitch = max(1.0, min(28.0, abs(float(params.get("max_pitch", 14.0)))))
            max_wrist = max(0.0, min(18.0, abs(float(params.get("max_wrist_pitch", 8.0)))))
            debug_view_enabled = _bool_param(params.get("debug_view"), default=True)
            log_events = _bool_param(params.get("log_events"), default=True)
            tracker = CatToyTracker(marker_color=marker_color)
        except ValueError as exc:
            return SkillResult(status="error", message=str(exc))

        energy_scale = {"gentle": 0.72, "normal": 1.0, "active": 1.22}.get(energy, 1.0)
        frame_interval = 1.0 / camera_fps
        estimator = CatPlayStateEstimator()
        debug_view = CatTeaserDebugView(enabled=debug_view_enabled)
        event_tracker = _CatTeaserEventTracker(marker_color=marker_color, enabled=log_events)
        source = self._frame_source_factory()
        cancel_event = asyncio.Event()
        self._cancel_event = cancel_event
        self._source = source
        self._motion = ctx.motion

        anchor = {
            "base_yaw": ctx.state.get("base_yaw", 0.0),
            "base_pitch": ctx.state.get("base_pitch", 0.0),
            "wrist_pitch": ctx.state.get("wrist_pitch", 0.0),
        }
        state_counts: Counter[str] = Counter()
        frames = 0
        marker_seen = 0
        pounces = 0
        catches = 0
        engagement_peak = 0.0
        missing_frames = 0
        stop_reason = "duration"
        pause_until = 0.0

        try:
            await asyncio.to_thread(source.start)
            started_at = time.monotonic()
            end_at = started_at + duration
            if log_events:
                print(
                    f"[cat_teaser] 0.0秒开始逗猫 marker_color={marker_color} "
                    f"camera={source.device_label} debug_view={debug_view_enabled}",
                    flush=True,
                )
            while not cancel_event.is_set() and time.monotonic() < end_at:
                tick_started = time.monotonic()
                frame = await asyncio.to_thread(source.read)
                if frame is None:
                    missing_frames += 1
                    if missing_frames >= max(2, int(camera_fps * 2)):
                        stop_reason = "camera_lost"
                        break
                    await asyncio.sleep(frame_interval)
                    continue
                missing_frames = 0
                frames += 1

                marker = tracker.track(frame)
                observation = estimator.update(frame, marker, timestamp=tick_started)
                state_counts[observation.state] += 1
                marker_seen += 1 if marker is not None else 0
                pounces += 1 if observation.state == "pounce" else 0
                catches += 1 if observation.state == "caught" else 0
                engagement_peak = max(engagement_peak, observation.engagement_score)

                if observation.state in {"caught", "unsafe_close", "pounce"}:
                    pause_until = max(pause_until, tick_started + self._pause_duration(observation.state))
                elapsed_s = tick_started - started_at
                event_text = event_tracker.update(observation, elapsed_s=elapsed_s)
                debug_stop = debug_view.render(
                    frame,
                    observation,
                    elapsed_s=elapsed_s,
                    marker_color=marker_color,
                    event_text=event_text,
                )
                if debug_stop:
                    stop_reason = "debug_view_closed"
                    break
                target = self._target_for_observation(
                    observation,
                    anchor=anchor,
                    now=tick_started,
                    started_at=started_at,
                    pause_until=pause_until,
                    energy_scale=energy_scale,
                    max_yaw=max_yaw,
                    max_pitch=max_pitch,
                    max_wrist=max_wrist,
                )
                ctx.motion.update_target(
                    MotionTarget(
                        joints=target,
                        max_velocity=self._velocity_for_state(observation.state, energy_scale),
                        style="linear",
                    )
                )

                elapsed = time.monotonic() - tick_started
                if elapsed < frame_interval:
                    await asyncio.sleep(frame_interval - elapsed)

            if cancel_event.is_set():
                stop_reason = "cancelled"
            if frames == 0:
                return SkillResult(status="error", message="cat_teaser camera did not provide frames")
            return SkillResult(
                status="cancelled" if stop_reason == "cancelled" else "ok",
                data={
                    "duration": round(time.monotonic() - started_at, 2),
                    "frames": frames,
                    "marker_seen": marker_seen,
                    "state_counts": dict(state_counts),
                    "engagement_peak": round(engagement_peak, 3),
                    "pounces": pounces,
                    "caught": catches,
                    "event_counts": event_tracker.counts(),
                    "events": event_tracker.events[-40:],
                    "stop_reason": stop_reason,
                    "camera": source.device_label,
                    "marker_color": marker_color,
                    "energy": energy,
                    "debug_view": debug_view_enabled,
                    "log_events": log_events,
                },
            )
        except ValueError as exc:
            return SkillResult(status="error", message=str(exc))
        except CatTeaserDependencyError as exc:
            return SkillResult(status="error", message=str(exc))
        except CatTeaserCameraError as exc:
            return SkillResult(status="error", message=str(exc))
        except Exception as exc:
            logger.exception("cat_teaser.failed")
            return SkillResult(status="error", message=str(exc))
        finally:
            try:
                ctx.motion.stop_smooth()
            finally:
                debug_view.close()
                await asyncio.to_thread(source.close)
                self._cancel_event = None
                self._source = None
                self._motion = None

    async def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._motion is not None:
            self._motion.stop_smooth()
        if self._source is not None:
            await asyncio.to_thread(self._source.close)

    def _target_for_observation(
        self,
        observation: CatPlayObservation,
        *,
        anchor: dict[str, float],
        now: float,
        started_at: float,
        pause_until: float,
        energy_scale: float,
        max_yaw: float,
        max_pitch: float,
        max_wrist: float,
    ) -> dict[str, float]:
        state = observation.state
        if state == "unsafe_close":
            offsets = {"base_yaw": 0.0, "base_pitch": -max_pitch * 0.55, "wrist_pitch": -max_wrist * 0.7}
            return self._bounded_target(anchor, offsets)
        if now < pause_until:
            offsets = {"base_yaw": 0.0, "base_pitch": -max_pitch * 0.35, "wrist_pitch": -max_wrist * 0.45}
            return self._bounded_target(anchor, offsets)

        elapsed = now - started_at
        state_amp = {
            "searching": 0.42,
            "teasing": 0.55,
            "engaged": 0.88,
            "pounce": 0.35,
            "caught": 0.18,
            "rest": 0.28,
            "unsafe_close": 0.0,
        }.get(state, 0.5)
        state_rate = {
            "searching": 1.0,
            "teasing": 1.25,
            "engaged": 1.85,
            "pounce": 0.8,
            "caught": 0.65,
            "rest": 0.55,
            "unsafe_close": 0.0,
        }.get(state, 1.0)
        amp = state_amp * energy_scale
        phase = elapsed * state_rate
        yaw_offset = math.sin(phase) * max_yaw * amp
        pitch_offset = math.sin(phase * 1.9 + math.pi / 2.0) * max_pitch * amp * 0.55
        wrist_offset = math.cos(phase * 1.35) * max_wrist * amp

        if observation.marker is not None:
            dx = observation.marker.normalized_x - 0.5
            dy = observation.marker.normalized_y - 0.5
            yaw_offset += dx * max_yaw * 0.35
            pitch_offset += dy * max_pitch * 0.25

        if state == "pounce":
            yaw_offset *= 0.35
            pitch_offset -= max_pitch * 0.3
            wrist_offset -= max_wrist * 0.45

        offsets = {
            "base_yaw": yaw_offset,
            "base_pitch": pitch_offset,
            "wrist_pitch": wrist_offset,
        }
        return self._bounded_target(anchor, offsets)

    @staticmethod
    def _bounded_target(anchor: dict[str, float], offsets: dict[str, float]) -> dict[str, float]:
        target: dict[str, float] = {}
        for joint, offset in offsets.items():
            value = anchor.get(joint, 0.0) + float(offset)
            limits = DEFAULT_JOINT_LIMITS.get(joint)
            if limits is not None:
                value = max(limits.min, min(limits.max, value))
            target[joint] = value
        return target

    @staticmethod
    def _velocity_for_state(state: CatPlayState, energy_scale: float) -> float:
        base = {
            "searching": 34.0,
            "teasing": 42.0,
            "engaged": 62.0,
            "pounce": 38.0,
            "caught": 26.0,
            "rest": 24.0,
            "unsafe_close": 22.0,
        }.get(state, 36.0)
        return max(18.0, min(78.0, base * energy_scale))

    @staticmethod
    def _pause_duration(state: CatPlayState) -> float:
        if state == "unsafe_close":
            return 0.9
        if state == "caught":
            return 0.65
        return 0.35


class _CatTeaserEventTracker:
    def __init__(self, *, marker_color: str, enabled: bool) -> None:
        self.marker_color = marker_color
        self.enabled = enabled
        self.events: list[dict[str, Any]] = []
        self._last_state: CatPlayState | None = None
        self._last_marker_seen = False
        self._last_emitted_at: dict[str, float] = {}

    def update(self, observation: CatPlayObservation, *, elapsed_s: float) -> str | None:
        marker_seen = observation.marker is not None
        event_type, description = self._event_for_observation(observation, marker_seen)
        self._last_state = observation.state
        self._last_marker_seen = marker_seen
        if event_type is None or description is None:
            return None
        if not self.enabled or not self._should_emit(event_type, elapsed_s):
            return description

        message = f"{elapsed_s:.1f}秒{description}"
        record = {
            "time_s": round(elapsed_s, 2),
            "type": event_type,
            "message": message,
            "state": observation.state,
            "motion_energy": round(observation.motion_energy, 4),
            "engagement_score": round(observation.engagement_score, 4),
            "marker_seen": marker_seen,
        }
        self.events.append(record)
        self._last_emitted_at[event_type] = elapsed_s
        print(
            "[cat_teaser] "
            f"{message} state={observation.state} motion={observation.motion_energy:.3f} "
            f"engagement={observation.engagement_score:.2f} marker={'seen' if marker_seen else 'lost'}",
            flush=True,
        )
        logger.info("cat_teaser.event", **record)
        return description

    def counts(self) -> dict[str, int]:
        return dict(Counter(str(event["type"]) for event in self.events))

    def _event_for_observation(
        self,
        observation: CatPlayObservation,
        marker_seen: bool,
    ) -> tuple[str | None, str | None]:
        if observation.state == "caught":
            return "touch", "有触碰动作"
        if observation.state == "pounce":
            return "pounce", "有扑击动作"
        if observation.state == "unsafe_close":
            return "unsafe_close", "距离过近，暂停撤离"
        if marker_seen and not self._last_marker_seen:
            return "marker_seen", f"识别到{self.marker_color}标记"
        if not marker_seen and self._last_marker_seen:
            return "marker_lost", f"{self.marker_color}标记丢失"
        if observation.state == "engaged" and self._last_state not in {"engaged", "pounce", "caught"}:
            return "engaged", "进入互动状态"
        return None, None

    def _should_emit(self, event_type: str, elapsed_s: float) -> bool:
        last = self._last_emitted_at.get(event_type)
        if last is None:
            return True
        gap = {
            "touch": 0.65,
            "pounce": 0.65,
            "unsafe_close": 1.0,
            "engaged": 1.2,
            "marker_seen": 1.0,
            "marker_lost": 1.0,
        }.get(event_type, 1.0)
        return elapsed_s - last >= gap


def _bool_param(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    return default
