"""VAD — Voice Activity Detection (adaptive energy-based).

Auto-calibrates to ambient noise during the first few seconds, then uses
a dynamic threshold = noise_floor * speech_ratio.  The noise floor is
continuously updated during silence so it tracks slow environmental changes.
"""

from __future__ import annotations

import struct
from collections import deque

import structlog

logger = structlog.get_logger(__name__)

CALIBRATION_FRAMES = 50
NOISE_FLOOR_ALPHA = 0.02
DEFAULT_SPEECH_RATIO = 1.8
MIN_ABSOLUTE_THRESHOLD = 15.0
# Only update noise floor with frames whose energy is close to the current
# floor. This prevents undetected speech from contaminating the estimate.
NOISE_UPDATE_CEILING = 2.0


class EnergyVAD:
    """Adaptive energy-based voice activity detector.

    Works on 16-bit mono PCM at any sample rate.

    During the first ``calibration_frames`` chunks, collects noise floor
    samples.  After calibration, speech threshold = noise_floor * speech_ratio.
    The noise floor keeps updating (EMA) during silence so it adapts to
    gradual ambient changes — but only with frames that are near the current
    floor, to avoid incorporating undetected speech.
    """

    def __init__(
        self,
        speech_ratio: float = DEFAULT_SPEECH_RATIO,
        silence_frames: int = 30,
        min_speech_frames: int = 3,
        calibration_frames: int = CALIBRATION_FRAMES,
    ) -> None:
        self._speech_ratio = speech_ratio
        self._silence_limit = silence_frames
        self._min_speech = min_speech_frames
        self._calibration_target = calibration_frames

        self._calibration_buf: deque[float] = deque(maxlen=calibration_frames)
        self._calibrated = False
        self._noise_floor = 0.0
        self._threshold = MIN_ABSOLUTE_THRESHOLD

        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False
        self._frame_count = 0

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    def process_chunk(self, pcm_chunk: bytes) -> bool:
        """Process a chunk of PCM audio. Returns True if speech is active."""
        energy = self._rms(pcm_chunk)
        self._frame_count += 1

        if self._calibrated and self._frame_count % 100 == 0:
            logger.info(
                "vad.energy",
                rms=round(energy, 1),
                threshold=round(self._threshold, 1),
                noise_floor=round(self._noise_floor, 1),
                speaking=self._is_speaking,
            )

        if not self._calibrated:
            self._calibration_buf.append(energy)
            if len(self._calibration_buf) >= self._calibration_target:
                self._finish_calibration()
            return False

        if energy > self._threshold:
            self._speech_frames += 1
            self._silence_frames = 0
            if self._speech_frames >= self._min_speech:
                self._is_speaking = True
        else:
            self._silence_frames += 1
            if not self._is_speaking:
                # Only incorporate truly quiet frames into the noise floor
                # estimate. This prevents undetected speech (energy above floor
                # but below threshold) from pushing the floor up.
                noise_ceiling = self._noise_floor * NOISE_UPDATE_CEILING
                if energy <= noise_ceiling or noise_ceiling < 1.0:
                    self._noise_floor = (1 - NOISE_FLOOR_ALPHA) * self._noise_floor + NOISE_FLOOR_ALPHA * energy
                    self._threshold = max(self._noise_floor * self._speech_ratio, MIN_ABSOLUTE_THRESHOLD)
            if self._silence_frames >= self._silence_limit:
                if self._is_speaking:
                    self._is_speaking = False
                self._speech_frames = 0

        return self._is_speaking

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def reset(self) -> None:
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False

    def _finish_calibration(self) -> None:
        samples = list(self._calibration_buf)
        if samples:
            self._noise_floor = sum(samples) / len(samples)
        self._threshold = max(self._noise_floor * self._speech_ratio, MIN_ABSOLUTE_THRESHOLD)
        self._calibrated = True
        logger.info(
            "vad.calibrated",
            noise_floor=round(self._noise_floor, 1),
            threshold=round(self._threshold, 1),
            speech_ratio=self._speech_ratio,
            samples=len(samples),
        )

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if len(pcm) < 2:
            return 0.0
        n_samples = len(pcm) // 2
        samples = struct.unpack(f"<{n_samples}h", pcm[: n_samples * 2])
        if not samples:
            return 0.0
        return (sum(s * s for s in samples) / n_samples) ** 0.5
