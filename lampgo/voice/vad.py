"""VAD — Voice Activity Detection (energy-based)."""

from __future__ import annotations

import struct

import structlog

logger = structlog.get_logger(__name__)


class EnergyVAD:
    """Simple energy-based voice activity detector.

    Works on 16-bit mono PCM at any sample rate.
    """

    def __init__(
        self,
        energy_threshold: float = 300.0,
        silence_frames: int = 30,
        min_speech_frames: int = 5,
    ) -> None:
        self._threshold = energy_threshold
        self._silence_limit = silence_frames
        self._min_speech = min_speech_frames
        self._speech_frames = 0
        self._silence_frames = 0
        self._is_speaking = False

    def process_chunk(self, pcm_chunk: bytes) -> bool:
        """Process a chunk of PCM audio. Returns True if speech is active."""
        energy = self._rms(pcm_chunk)

        if energy > self._threshold:
            self._speech_frames += 1
            self._silence_frames = 0
            if self._speech_frames >= self._min_speech:
                self._is_speaking = True
        else:
            self._silence_frames += 1
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

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if len(pcm) < 2:
            return 0.0
        n_samples = len(pcm) // 2
        samples = struct.unpack(f"<{n_samples}h", pcm[: n_samples * 2])
        if not samples:
            return 0.0
        return (sum(s * s for s in samples) / n_samples) ** 0.5
