"""STT — Speech-to-Text via Volcengine ASR."""

from __future__ import annotations

import base64
import io
import uuid
import wave
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from lampgo.core.config import LampgoConfig

logger = structlog.get_logger(__name__)

VOLCENGINE_ASR_FLASH_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
VOLCENGINE_ASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
DEFAULT_MODEL = "bigmodel"


class VolcengineASR:
    """Transcribe short recorded audio via Volcengine bigmodel ASR.

    lampgo's browser and wake-loop paths already upload a complete WAV buffer,
    so the low-latency flash endpoint is the best fit: it accepts base64 audio
    directly and returns the transcript in a single response.
    """

    def __init__(
        self,
        app_id: str,
        access_token: str,
        model: str = DEFAULT_MODEL,
        endpoint: str = VOLCENGINE_ASR_FLASH_URL,
    ) -> None:
        self._app_id = app_id.strip()
        self._access_token = access_token.strip()
        self._model = (model or "").strip() or DEFAULT_MODEL
        self._endpoint = endpoint

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe raw PCM 16-bit mono audio to text."""
        wav_buf = _pcm_to_wav(audio_bytes, sample_rate)
        b64_audio = base64.b64encode(wav_buf).decode("ascii")
        return await self.transcribe_wav_b64(b64_audio)

    async def transcribe_wav_b64(self, wav_b64: str) -> str:
        """Transcribe base64-encoded WAV to text."""
        if not self._app_id or not self._access_token:
            logger.warning("stt.volcengine_missing_credentials")
            return ""
        return await self._call_api(wav_b64)

    async def _call_api(self, b64_audio: str) -> str:
        try:
            import httpx
        except ImportError:
            logger.warning("stt.no_httpx")
            return ""

        request_id = str(uuid.uuid4())
        body = {
            "user": {"uid": self._app_id},
            "audio": {"data": b64_audio},
            "request": {
                "model_name": self._model,
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
            },
        }
        headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": VOLCENGINE_ASR_RESOURCE_ID,
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
            "Content-Type": "application/json",
        }

        logger.info("stt.volcengine_calling_api", request_id=request_id, audio_b64_len=len(b64_audio))
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._endpoint, json=body, headers=headers)
                status_code = resp.headers.get("X-Api-Status-Code", "")
                status_message = resp.headers.get("X-Api-Message", "")
                log_id = resp.headers.get("X-Tt-Logid", "")
                if status_code and status_code != "20000000":
                    logger.warning(
                        "stt.volcengine_status_error",
                        request_id=request_id,
                        status_code=status_code,
                        status_message=status_message,
                        log_id=log_id,
                    )
                    return ""
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("stt.volcengine_request_failed", request_id=request_id, model=self._model)
            return ""

        text = _extract_text(data)
        if text:
            logger.info("stt.volcengine_transcribed", request_id=request_id, text=text[:80], model=self._model)
        else:
            logger.warning("stt.volcengine_empty_result", request_id=request_id, keys=list(data.keys()))
        return text


def build_stt(config: LampgoConfig) -> VolcengineASR:
    """Construct the STT backend from voice config."""
    model = config.voice.stt_model or DEFAULT_MODEL
    logger.info("stt.init", provider="volcengine", model=model)
    return VolcengineASR(
        app_id=config.voice.volcengine_app_id,
        access_token=config.voice.volcengine_access_token,
        model=model,
    )


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _extract_text(data: dict) -> str:
    result = data.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        utterances = result.get("utterances")
        if isinstance(utterances, list):
            parts = []
            for item in utterances:
                if isinstance(item, dict):
                    utterance_text = item.get("text")
                    if isinstance(utterance_text, str) and utterance_text.strip():
                        parts.append(utterance_text.strip())
            if parts:
                return "".join(parts).strip()

    text = data.get("text")
    return text.strip() if isinstance(text, str) else ""
