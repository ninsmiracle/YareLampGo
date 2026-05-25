"""Audio-reactive factory skills."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

import structlog

from lampgo.core.types import SkillResult
from lampgo.perception.music import (
    AudioSourceError,
    BeatDecision,
    BeatGate,
    DancePhraseRenderer,
    MusicFeatureExtractor,
    MusicFeatures,
    make_music_source,
)
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)

_FPS = 50


class DanceToMusicSkill(Skill):
    """Dance to live music using beat-gated, visible motion phrases."""

    skill_id = "dance_to_music"
    label = "跟音乐跳舞"
    description = "Listen to the computer music stream and sway on selected beats."
    parameters = {
        "duration": ParameterSpec(
            name="duration",
            type="float",
            required=False,
            default=60.0,
            description="Seconds to dance; 0 keeps dancing until cancelled.",
        ),
        "source": ParameterSpec(
            name="source",
            type="str",
            required=False,
            default="system",
            description="Audio source: system (macOS ScreenCaptureKit), mic, blackhole, or synthetic.",
        ),
        "style": ParameterSpec(
            name="style",
            type="str",
            required=False,
            default="jazz",
            description="Dance style: jazz, electronic, or ambient.",
        ),
        "sensitivity": ParameterSpec(
            name="sensitivity",
            type="float",
            required=False,
            default=1.0,
            description="Audio-to-motion sensitivity multiplier.",
        ),
        "amplitude": ParameterSpec(
            name="amplitude",
            type="float",
            required=False,
            default=1.0,
            description="Global motion amplitude multiplier.",
        ),
        "beat_stride": ParameterSpec(
            name="beat_stride",
            type="int",
            required=False,
            default=0,
            description="Respond every N beats; 0 lets LampGo auto-skip fast beats.",
        ),
        "led": ParameterSpec(
            name="led",
            type="bool",
            required=False,
            default=True,
            description="Whether to flash music/star LED accents when available.",
        ),
    }

    def __init__(self) -> None:
        self._cancel_event: asyncio.Event | None = None
        self._source = None
        self._motion = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        duration = max(0.0, float(params.get("duration", 60.0)))
        source_name = str(params.get("source") or "system")
        style = str(params.get("style") or "jazz").strip().lower()
        sensitivity = max(0.0, float(params.get("sensitivity", 1.0)))
        amplitude = max(0.0, float(params.get("amplitude", 1.0)))
        beat_stride = int(params.get("beat_stride", 0) or 0)
        led_enabled = _coerce_bool(params.get("led", True))

        source = make_music_source(source_name)
        gate = BeatGate(beat_stride=beat_stride)
        renderer = DancePhraseRenderer(style=style, fps=_FPS)
        features: deque[MusicFeatures] = deque(maxlen=18)
        pending_accent: BeatDecision | None = None
        cancel_event = asyncio.Event()
        self._cancel_event = cancel_event
        self._source = source
        self._motion = ctx.motion
        anchor = dict(ctx.motion.current_state.positions or ctx.state.positions)
        groove_interval_s = 0.62
        groove_duration_s = 0.72
        accent_duration_s = 0.56
        min_phrase_gap_s = 0.26
        next_groove_at = time.monotonic()
        last_phrase_at = -1e9
        started_at = time.monotonic()
        end_at = started_at + duration if duration > 0 else float("inf")
        chunks = 0
        phrases = 0
        accents = 0

        try:
            await source.start()
            extractor = MusicFeatureExtractor(sample_rate=source.sample_rate, channels=source.channels)
            while not cancel_event.is_set() and time.monotonic() < end_at:
                chunk = await source.read_chunk()
                if not chunk:
                    await asyncio.sleep(0.01)
                    continue
                chunks += 1
                for item in extractor.push_pcm(chunk):
                    features.append(item)
                    decision = gate.consider(item)
                    if decision.accent:
                        pending_accent = decision

                now = time.monotonic()
                if not features:
                    continue
                if pending_accent is not None and now - pending_accent.timestamp > 0.45:
                    pending_accent = None

                should_accent = pending_accent is not None and now - last_phrase_at >= min_phrase_gap_s
                should_groove = now >= next_groove_at
                if not should_accent and not should_groove:
                    continue

                beat = pending_accent if should_accent else None
                frames = renderer.render(
                    anchor=anchor,
                    features=list(features),
                    beat=beat,
                    duration_s=accent_duration_s if beat is not None else groove_duration_s,
                    amplitude_scale=sensitivity * amplitude,
                )
                if should_accent:
                    pending_accent = None
                if not frames:
                    next_groove_at = now + 0.1
                    continue

                last_phrase_at = now
                next_groove_at = now + (0.36 if beat is not None else groove_interval_s)
                ctx.motion.stream_frames(frames, fps=_FPS, playback_mode="expressive")
                phrases += 1
                if beat is not None and beat.accent:
                    accents += 1
                    if led_enabled:
                        self._flash_led(ctx, beat)

            ctx.motion.stop_smooth()
            return SkillResult(
                status="ok",
                data={
                    "source": source_name,
                    "style": style,
                    "duration": round(time.monotonic() - started_at, 2),
                    "chunks": chunks,
                    "phrases": phrases,
                    "accents": accents,
                    "sample_rate": source.sample_rate,
                    "channels": source.channels,
                },
            )
        except AudioSourceError as exc:
            logger.warning("dance_to_music.audio_source_error", source=source_name, error=str(exc))
            return SkillResult(status="error", message=str(exc))
        finally:
            try:
                await source.stop()
            finally:
                self._cancel_event = None
                self._source = None
                self._motion = None

    async def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._motion is not None:
            self._motion.stop_smooth()
        if self._source is not None:
            try:
                await self._source.stop()
            except Exception:
                logger.exception("dance_to_music.stop_source_failed")

    def _flash_led(self, ctx: SkillContext, beat: BeatDecision) -> None:
        if not ctx.led.is_connected:
            return
        expression = "music" if beat.intensity < 0.65 else "star"
        try:
            ctx.led.set_mode(expression)
        except Exception:
            logger.debug("dance_to_music.led_accent_failed", exc_info=True)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
