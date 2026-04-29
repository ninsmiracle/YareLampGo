"""Wake word detection using openWakeWord.

Wraps the ``openwakeword`` library to detect a configurable wake word
(default: ``hey_jarvis``) from a continuous 16 kHz PCM16LE mono audio
stream.  Each call to :meth:`feed` ingests a chunk of raw PCM and
returns ``True`` when the wake word is detected above the threshold.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import numpy as np
import structlog

# openwakeword's __init__ unconditionally imports custom_verifier_model which
# pulls in scipy + sklearn.  We only need Model and utils, so inject a stub
# for that entire submodule before the package is first imported.
_STUB_MOD = "openwakeword.custom_verifier_model"
if _STUB_MOD not in sys.modules:
    _stub = types.ModuleType(_STUB_MOD)
    _stub.train_custom_verifier = None  # type: ignore[attr-defined]
    sys.modules[_STUB_MOD] = _stub

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "hey_jarvis"
DEFAULT_THRESHOLD = 0.5
SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # openwakeword expects 80ms frames (16000 * 0.08)


class WakeWordDetector:
    """Stateful wake-word detector fed with raw PCM16LE chunks."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._model_name = model_name
        self._threshold = threshold
        self._oww: Any = None
        self._buf = bytearray()
        self._ready = False
        self._init_model()

    def _init_model(self) -> None:
        try:
            from openwakeword.utils import download_models
            from openwakeword.model import Model

            download_models([self._model_name])
            self._oww = Model(wakeword_models=[self._model_name], inference_framework="onnx")
            self._ready = True
            logger.info(
                "wakeword.init_ok",
                model=self._model_name,
                threshold=self._threshold,
            )
        except Exception:
            logger.exception("wakeword.init_failed", model=self._model_name)
            self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def feed(self, pcm_chunk: bytes) -> bool:
        """Feed raw PCM16LE mono 16 kHz audio. Returns True on wake-word detection."""
        if not self._ready or self._oww is None:
            return False

        self._buf.extend(pcm_chunk)
        detected = False

        while len(self._buf) >= FRAME_SAMPLES * 2:
            frame_bytes = bytes(self._buf[: FRAME_SAMPLES * 2])
            del self._buf[: FRAME_SAMPLES * 2]

            samples = np.frombuffer(frame_bytes, dtype=np.int16)
            prediction = self._oww.predict(samples)

            for model_key, score in prediction.items():
                if score >= self._threshold:
                    logger.info(
                        "wakeword.detected",
                        model=model_key,
                        score=f"{score:.3f}",
                        threshold=self._threshold,
                    )
                    detected = True

        return detected

    def reset(self) -> None:
        """Clear internal audio buffer and model state."""
        self._buf.clear()
        if self._oww is not None:
            self._oww.reset()
