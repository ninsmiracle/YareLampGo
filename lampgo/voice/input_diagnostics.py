"""Observe the Agent input boundary without retaining or changing audio."""

from __future__ import annotations

import asyncio
import logging
import math
import time

logger = logging.getLogger(__name__)


def instrument_stt_node(original, *, interval: float = 5.0):
    async def observed(agent, audio, model_settings):
        frames = 0
        samples = 0
        last_frame = time.monotonic()
        peak = rms = 0.0
        input_ended = False
        stream_id = f"{id(audio):x}"

        async def metered_audio():
            nonlocal frames, samples, last_frame, peak, rms, input_ended
            try:
                async for frame in audio:
                    data = frame.data
                    frames += 1
                    samples += len(data)
                    last_frame = time.monotonic()
                    if len(data):
                        peak = max(peak, max(abs(v) for v in data) / 32768)
                        rms = max(rms, math.sqrt(sum(v * v for v in data) / len(data)) / 32768)
                    yield frame
            finally:
                input_ended = True

        async def report():
            nonlocal peak, rms
            while True:
                await asyncio.sleep(interval)
                logger.info(
                    "voice.agent_input stream=%s frames=%d samples=%d gap_s=%.2f "
                    "rms_max=%.4f peak_max=%.4f input_ended=%s",
                    stream_id, frames, samples, time.monotonic() - last_frame,
                    rms, peak, input_ended,
                )
                peak = rms = 0.0

        logger.info("voice.agent_input_started stream=%s", stream_id)
        timer = asyncio.create_task(report(), name="lampgo-agent-input-diagnostics")
        source = metered_audio()
        stream = original(agent, source, model_settings)
        try:
            async for event in stream:
                logger.info("voice.agent_stt_event stream=%s type=%s", stream_id, event.type)
                yield event
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
            try:
                await stream.aclose()
            finally:
                await source.aclose()
                logger.info("voice.agent_input_closed stream=%s frames=%d", stream_id, frames)

    return observed


def log_vad_metrics(event):
    metrics = event.metrics
    if metrics.type == "vad_metrics":
        logger.info(
            "voice.agent_vad inference_count=%d inference_s=%.3f idle_s=%.2f",
            metrics.inference_count, metrics.inference_duration_total, metrics.idle_time,
        )
