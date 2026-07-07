"""Local vision cat teaser skill."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
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
from lampgo.personastore import lampgo_home
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
            default="red",
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
        "save_recording": ParameterSpec(
            name="save_recording",
            type="bool",
            required=False,
            default=True,
            description="Save camera video from this cat_teaser run as a local MP4 session recording.",
        ),
        "recording_dir": ParameterSpec(
            name="recording_dir",
            type="str",
            required=False,
            default="",
            description=(
                "Base directory for saved cat_teaser camera sessions; "
                "empty uses ~/.lampgo/cat_teaser_recordings."
            ),
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
            marker_color = str(params.get("marker_color") or "red").strip().lower()
            energy = str(params.get("energy") or "normal").strip().lower()
            camera_fps = max(2.0, min(12.0, float(params.get("camera_fps", 8.0))))
            max_yaw = max(1.0, min(45.0, abs(float(params.get("max_yaw", 25.0)))))
            max_pitch = max(1.0, min(28.0, abs(float(params.get("max_pitch", 14.0)))))
            max_wrist = max(0.0, min(18.0, abs(float(params.get("max_wrist_pitch", 8.0)))))
            debug_view_enabled = _bool_param(params.get("debug_view"), default=True)
            log_events = _bool_param(params.get("log_events"), default=True)
            save_recording = _bool_param(params.get("save_recording"), default=True)
            recording_dir = str(params.get("recording_dir") or "").strip()
            tracker = CatToyTracker(marker_color=marker_color)
        except ValueError as exc:
            return SkillResult(status="error", message=str(exc))

        energy_scale = {"gentle": 0.72, "normal": 1.0, "active": 1.22}.get(energy, 1.0)
        frame_interval = 1.0 / camera_fps
        estimator = CatPlayStateEstimator()
        debug_view = CatTeaserDebugView(enabled=debug_view_enabled)
        event_tracker = _CatTeaserEventTracker(marker_color=marker_color, enabled=log_events)
        recorder = _CatTeaserFrameRecorder(
            enabled=save_recording,
            base_dir=_recording_base_dir(recording_dir),
            marker_color=marker_color,
            camera_fps=camera_fps,
        )
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
        self_motion_suppression_until = 0.0
        self_motion_suppressed = 0
        previous_motion_target: dict[str, float] | None = dict(anchor)
        escape_target: dict[str, float] | None = None
        escape_until = 0.0
        last_escape_at = 0.0
        escape_direction = -1.0
        escape_dashes = 0

        try:
            await asyncio.to_thread(source.start)
            started_at = time.monotonic()
            end_at = started_at + duration
            recorder.set_camera(source.device_label)
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
                raw_observation = estimator.update(frame, marker, timestamp=tick_started)
                observation = self._suppress_self_motion_observation(
                    raw_observation,
                    now=tick_started,
                    suppression_until=self_motion_suppression_until,
                )
                if observation.state != raw_observation.state:
                    self_motion_suppressed += 1
                state_counts[observation.state] += 1
                marker_seen += 1 if marker is not None else 0
                pounces += 1 if observation.state == "pounce" else 0
                catches += 1 if observation.state == "caught" else 0
                engagement_peak = max(engagement_peak, observation.engagement_score)

                if observation.state == "unsafe_close":
                    escape_target = None
                    escape_until = 0.0
                if observation.state in {"caught", "unsafe_close", "pounce"}:
                    pause_until = max(pause_until, tick_started + self._pause_duration(observation.state))
                elapsed_s = tick_started - started_at
                if observation.state == "caught" and tick_started - last_escape_at >= self._escape_cooldown():
                    escape_direction = self._escape_direction(observation, previous_direction=escape_direction)
                    escape_target = self._escape_target(
                        anchor=anchor,
                        direction=escape_direction,
                        max_yaw=max_yaw,
                        max_pitch=max_pitch,
                        max_wrist=max_wrist,
                    )
                    escape_until = tick_started + self._escape_duration(energy_scale)
                    last_escape_at = tick_started
                    escape_dashes += 1
                    pause_until = max(pause_until, escape_until + 0.12)
                    direction_label = "right" if escape_direction > 0 else "left"
                    if log_events:
                        print(
                            "[cat_teaser] "
                            f"{elapsed_s:.1f}秒触碰后快速横向逃逸 direction={direction_label}",
                            flush=True,
                        )
                    logger.info(
                        "cat_teaser.escape_dash",
                        time_s=round(elapsed_s, 2),
                        direction=direction_label,
                        escape_dashes=escape_dashes,
                    )
                event_text = event_tracker.update(observation, elapsed_s=elapsed_s)
                recording_notice = recorder.write_frame(
                    frame,
                    observation=observation,
                    elapsed_s=elapsed_s,
                    event_text=event_text,
                )
                if log_events and recording_notice is not None:
                    print(recording_notice, flush=True)
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
                if tick_started < escape_until and escape_target is not None:
                    target = escape_target
                    max_velocity = self._escape_velocity(energy_scale)
                else:
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
                    max_velocity = self._velocity_for_state(observation.state, energy_scale)
                ctx.motion.update_target(
                    MotionTarget(
                        joints=target,
                        max_velocity=max_velocity,
                        style="linear",
                    )
                )
                self_motion_suppression_until = self._updated_self_motion_suppression_until(
                    previous_target=previous_motion_target,
                    current_target=target,
                    now=tick_started,
                    current_until=self_motion_suppression_until,
                )
                previous_motion_target = target

                elapsed = time.monotonic() - tick_started
                if elapsed < frame_interval:
                    await asyncio.sleep(frame_interval - elapsed)

            if cancel_event.is_set():
                stop_reason = "cancelled"
            if frames == 0:
                return SkillResult(status="error", message="cat_teaser camera did not provide frames")
            recorder.close(stop_reason=stop_reason)
            recording_summary = recorder.summary()
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
                    "escape_dashes": escape_dashes,
                    "self_motion_suppressed": self_motion_suppressed,
                    "event_counts": event_tracker.counts(),
                    "events": event_tracker.events[-40:],
                    "stop_reason": stop_reason,
                    "camera": source.device_label,
                    "marker_color": marker_color,
                    "energy": energy,
                    "debug_view": debug_view_enabled,
                    "log_events": log_events,
                    "recording": recording_summary,
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
                recorder.close(stop_reason=stop_reason)
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
    def _suppress_self_motion_observation(
        observation: CatPlayObservation,
        *,
        now: float,
        suppression_until: float,
    ) -> CatPlayObservation:
        if now >= suppression_until or observation.state == "unsafe_close":
            return observation
        if observation.state == "caught":
            state: CatPlayState = "engaged" if observation.marker is not None else "searching"
            return replace(
                observation,
                state=state,
                motion_energy=observation.motion_energy * 0.45,
            )
        if observation.state == "pounce" and observation.motion_energy < 0.45:
            return replace(
                observation,
                state="engaged",
                motion_energy=observation.motion_energy * 0.55,
            )
        return observation

    @staticmethod
    def _updated_self_motion_suppression_until(
        *,
        previous_target: dict[str, float] | None,
        current_target: dict[str, float],
        now: float,
        current_until: float,
    ) -> float:
        if previous_target is None:
            return current_until
        delta = CatTeaserSkill._target_motion_delta(previous_target, current_target)
        if delta < 6.5:
            return current_until
        window_s = min(0.38, max(0.18, 0.12 + delta * 0.012))
        return max(current_until, now + window_s)

    @staticmethod
    def _target_motion_delta(previous_target: dict[str, float], current_target: dict[str, float]) -> float:
        return (
            abs(current_target.get("base_yaw", 0.0) - previous_target.get("base_yaw", 0.0))
            + abs(current_target.get("base_pitch", 0.0) - previous_target.get("base_pitch", 0.0)) * 0.65
            + abs(current_target.get("wrist_pitch", 0.0) - previous_target.get("wrist_pitch", 0.0)) * 0.45
        )

    @staticmethod
    def _escape_direction(observation: CatPlayObservation, *, previous_direction: float) -> float:
        x: float | None = None
        if observation.motion_centroid is not None:
            x = observation.motion_centroid[0]
        elif observation.marker is not None:
            x = observation.marker.normalized_x
        if x is None or 0.45 <= x <= 0.55:
            return -previous_direction if previous_direction else 1.0
        return 1.0 if x < 0.5 else -1.0

    @staticmethod
    def _escape_duration(energy_scale: float) -> float:
        return max(0.26, min(0.48, 0.36 * energy_scale))

    @staticmethod
    def _escape_cooldown() -> float:
        return 1.15

    def _escape_target(
        self,
        *,
        anchor: dict[str, float],
        direction: float,
        max_yaw: float,
        max_pitch: float,
        max_wrist: float,
    ) -> dict[str, float]:
        yaw_span = min(max_yaw * 0.92, 28.0)
        yaw_offset = math.copysign(max(12.0, yaw_span), direction)
        offsets = {
            "base_yaw": yaw_offset,
            "base_pitch": -max_pitch * 0.28,
            "wrist_pitch": -max_wrist * 0.32,
        }
        return self._bounded_target(anchor, offsets)

    @staticmethod
    def _escape_velocity(energy_scale: float) -> float:
        return max(54.0, min(86.0, 78.0 * energy_scale))

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
            "contact_motion_energy": round(observation.contact_motion_energy, 4),
            "marker_disturbance": round(observation.marker_disturbance, 4),
            "engagement_score": round(observation.engagement_score, 4),
            "marker_seen": marker_seen,
        }
        self.events.append(record)
        self._last_emitted_at[event_type] = elapsed_s
        print(
            "[cat_teaser] "
            f"{message} state={observation.state} motion={observation.motion_energy:.3f} "
            f"contact={observation.contact_motion_energy:.3f} disturb={observation.marker_disturbance:.3f} "
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


class _CatTeaserFrameRecorder:
    def __init__(
        self,
        *,
        enabled: bool,
        base_dir: Path,
        marker_color: str,
        camera_fps: float,
    ) -> None:
        self.enabled = enabled
        self.base_dir = base_dir
        self.marker_color = marker_color
        self.camera_fps = camera_fps
        self.camera = ""
        self.session_dir: Path | None = None
        self.video_path: Path | None = None
        self.frames_written = 0
        self.error: str | None = None
        self._cv2 = None
        self._writer = None
        self._video_size: tuple[int, int] | None = None
        self._metadata: dict[str, Any] = {}
        self._invalid_frame_warned = False
        self._closed = False

    def set_camera(self, camera: str) -> None:
        self.camera = camera

    def write_frame(
        self,
        frame,
        *,
        observation: CatPlayObservation,
        elapsed_s: float,
        event_text: str | None,
    ) -> str | None:
        if not self.enabled or self._closed:
            return None
        if not self._is_image_frame(frame):
            if not self._invalid_frame_warned:
                self._invalid_frame_warned = True
                self.error = "camera frame is not an image; recording skipped"
            return None

        try:
            video_frame = self._prepare_video_frame(frame)
            if video_frame is None:
                return None
            notice = self._ensure_session(video_frame)
            if self.session_dir is None or self.video_path is None or video_frame is None:
                return notice

            writer = self._ensure_video_writer(video_frame)
            if writer is None:
                return notice

            frame_index = self.frames_written + 1
            writer.write(video_frame)
            self.frames_written = frame_index
            self._append_frame_record(frame_index, observation, elapsed_s, event_text)
            return notice
        except Exception as exc:
            self.error = f"recording failed: {exc}"
            self.enabled = False
            logger.warning("cat_teaser.recording_failed", error=str(exc), base_dir=str(self.base_dir))
            return None

    def close(self, *, stop_reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                logger.debug("cat_teaser.video_release_failed", exc_info=True)
            finally:
                self._writer = None
        if self.session_dir is None:
            return
        self._metadata.update(
            {
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "frames_written": self.frames_written,
                "stop_reason": stop_reason,
                "error": self.error,
            }
        )
        try:
            self._write_json(self.session_dir / "metadata.json", self._metadata)
        except Exception as exc:
            self.error = f"failed to finalize recording metadata: {exc}"
            logger.warning("cat_teaser.recording_finalize_failed", error=str(exc), path=str(self.session_dir))

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "path": str(self.session_dir) if self.session_dir is not None else None,
            "video_path": str(self.video_path) if self.video_path is not None else None,
            "frames_written": self.frames_written,
            "events_path": str(self.session_dir / "frames.jsonl") if self.session_dir is not None else None,
            "metadata_path": str(self.session_dir / "metadata.json") if self.session_dir is not None else None,
            "error": self.error,
        }

    @staticmethod
    def _is_image_frame(frame) -> bool:
        shape = getattr(frame, "shape", None)
        if shape is None or len(shape) < 2:
            return False
        return len(shape) == 2 or (len(shape) == 3 and shape[2] in {1, 3, 4})

    def _ensure_session(self, frame) -> str | None:
        if self.session_dir is not None:
            return None
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_dir = (self.base_dir / f"{timestamp}-{self.marker_color}").expanduser()
        session_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = session_dir
        self.video_path = session_dir / "cat_teaser.mp4"

        height, width = frame.shape[:2]
        self._metadata = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "marker_color": self.marker_color,
            "camera": self.camera,
            "camera_fps": self.camera_fps,
            "frame_width": int(width),
            "frame_height": int(height),
            "video": "cat_teaser.mp4",
            "video_codec": "mp4v",
            "frames_jsonl": "frames.jsonl",
            "frames_written": 0,
            "stop_reason": None,
            "error": None,
        }
        self._write_json(self.session_dir / "metadata.json", self._metadata)
        return f"[cat_teaser] 摄像头视频保存到 {self.video_path}"

    def _ensure_cv2(self):
        if self._cv2 is not None:
            return self._cv2
        try:
            import cv2
        except ImportError:
            self.error = "OpenCV is not available; recording skipped"
            return None
        self._cv2 = cv2
        return cv2

    def _prepare_video_frame(self, frame):
        cv2 = self._ensure_cv2()
        if cv2 is None:
            return None
        shape = getattr(frame, "shape", None)
        if shape is None or len(shape) < 2:
            return None
        if len(shape) == 2:
            out = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif len(shape) == 3 and shape[2] == 4:
            out = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        elif len(shape) == 3 and shape[2] == 1:
            out = cv2.cvtColor(frame[:, :, 0], cv2.COLOR_GRAY2BGR)
        else:
            out = frame
        if getattr(out, "dtype", None) != "uint8" and hasattr(out, "astype"):
            out = out.astype("uint8")
        height, width = out.shape[:2]
        even_width = width - (width % 2)
        even_height = height - (height % 2)
        if even_width <= 0 or even_height <= 0:
            return None
        if even_width != width or even_height != height:
            out = out[:even_height, :even_width]
        if self._video_size is not None and (out.shape[1], out.shape[0]) != self._video_size:
            out = cv2.resize(out, self._video_size)
        return out

    def _ensure_video_writer(self, frame):
        if self._writer is not None:
            return self._writer
        cv2 = self._ensure_cv2()
        if cv2 is None or self.video_path is None:
            return None
        height, width = frame.shape[:2]
        self._video_size = (int(width), int(height))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(self.video_path), fourcc, float(self.camera_fps), self._video_size)
        if not writer.isOpened():
            self.error = f"failed to open mp4 writer: {self.video_path}"
            self.enabled = False
            return None
        self._writer = writer
        return writer

    def _append_frame_record(
        self,
        frame_index: int,
        observation: CatPlayObservation,
        elapsed_s: float,
        event_text: str | None,
    ) -> None:
        if self.session_dir is None:
            return
        marker = observation.marker
        record = {
            "frame": frame_index,
            "elapsed_s": round(elapsed_s, 3),
            "state": observation.state,
            "motion_energy": round(observation.motion_energy, 4),
            "contact_motion_energy": round(observation.contact_motion_energy, 4),
            "marker_disturbance": round(observation.marker_disturbance, 4),
            "engagement_score": round(observation.engagement_score, 4),
            "motion_centroid": observation.motion_centroid,
            "event": event_text,
            "marker": None
            if marker is None
            else {
                "x": round(marker.x, 2),
                "y": round(marker.y, 2),
                "radius": round(marker.radius, 2),
                "area": round(marker.area, 2),
                "confidence": round(marker.confidence, 4),
            },
        }
        with (self.session_dir / "frames.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _recording_base_dir(value: str) -> Path:
    if value:
        return Path(value).expanduser()
    return lampgo_home() / "cat_teaser_recordings"


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
