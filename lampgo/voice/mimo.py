"""Shared MiMo speech primitives.

MiMo exposes ASR and TTS on its OpenAI-compatible ``/chat/completions``
endpoint.  This module deliberately takes the existing LLM base URL and API
key instead of creating a second voice credential surface.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from lampgo.core.config import LLMConfig, VoiceConfig


logger = structlog.get_logger(__name__)

DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_ASR_MODEL = "mimo-v2.5-asr"
DEFAULT_MIMO_TTS_MODEL = "mimo-v2.5-tts"
DEFAULT_MIMO_TTS_VOICE = "mimo_default"
MIMO_TTS_SAMPLE_RATE = 24_000

# Values produced by the previous Volcengine/Edge configuration.  Keeping the
# migration here means a device with an existing ``~/.lampgo/config.toml`` can
# switch providers without first hand-editing stale model or voice IDs.
_LEGACY_ASR_MODELS = {"", "bigmodel", "mimo-v2.5", "mimo-v2-omni"}
_LEGACY_TTS_MODELS = {
    "",
    "mimo-v2-tts",
    "seed-tts-1.0",
    "seed-tts-2.0",
    "seed-tts-2.0-standard",
    "seed-tts-2.0-expressive",
    "seed-icl-2.0",
    "volc.service_type.10029",
}


class MiMoAPIError(RuntimeError):
    """A provider error that preserves safe diagnostic metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = -1,
        request_id: str = "",
        body: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.body = body


@dataclass(frozen=True)
class MiMoSpeechSettings:
    """Non-secret speech routing settings derived from LampGo's LLM config."""

    api_base: str
    api_key: str
    asr_model: str = DEFAULT_MIMO_ASR_MODEL
    tts_model: str = DEFAULT_MIMO_TTS_MODEL
    tts_voice: str = DEFAULT_MIMO_TTS_VOICE
    tts_style_prompt: str = ""


@dataclass(frozen=True)
class MiMoASRResult:
    text: str
    request_id: str = ""


def build_mimo_speech_settings(
    llm: LLMConfig,
    voice: VoiceConfig,
) -> MiMoSpeechSettings:
    """Build MiMo speech settings from the *existing* LLM credential.

    Reusing an arbitrary non-MiMo provider key would be surprising and unsafe,
    so voice can start only when the configured LLM provider is MiMo.
    """

    provider = str(llm.normalize_provider_alias(llm.provider or "") or "").strip().lower()
    if provider != "mimo":
        raise ValueError("MiMo voice requires llm.provider = 'mimo'")
    api_key = (llm.api_key or "").strip()
    if not api_key:
        raise ValueError("MiMo voice requires the configured LLM API key")

    return MiMoSpeechSettings(
        api_base=_normalize_api_base(llm.api_base),
        api_key=api_key,
        asr_model=mimo_asr_model_or_default(voice.stt_model),
        tts_model=mimo_tts_model_or_default(voice.tts_model),
        tts_voice=mimo_tts_voice_or_default(voice.tts_voice),
        tts_style_prompt=(voice.tts_style_prompt or "").strip(),
    )


def _normalize_api_base(value: str) -> str:
    return ((value or "").strip() or DEFAULT_MIMO_BASE_URL).rstrip("/")


def mimo_asr_model_or_default(value: str | None) -> str:
    """Return a supported MiMo ASR model for new and migrated settings."""

    model = (value or "").strip()
    return DEFAULT_MIMO_ASR_MODEL if model.lower() in _LEGACY_ASR_MODELS else model


def mimo_tts_model_or_default(value: str | None) -> str:
    """Return a supported MiMo TTS model for new and migrated settings."""

    model = (value or "").strip()
    return DEFAULT_MIMO_TTS_MODEL if model.lower() in _LEGACY_TTS_MODELS else model


def mimo_tts_voice_or_default(value: str | None) -> str:
    """Drop known non-MiMo voice identifiers while preserving explicit IDs."""

    voice = (value or "").strip()
    lowered = voice.lower()
    if not voice or lowered in {"mimo_default", "bv700_streaming"}:
        return DEFAULT_MIMO_TTS_VOICE
    if (
        lowered.startswith(("zh_", "saturn_", "s_"))
        or lowered.endswith("_bigtts")
        or (voice.endswith("Neural") and "-" in voice)
    ):
        return DEFAULT_MIMO_TTS_VOICE
    return voice


def mimo_headers(api_key: str) -> dict[str, str]:
    """Use both documented MiMo and OpenAI-compatible auth headers."""

    return {
        "Authorization": f"Bearer {api_key}",
        "api-key": api_key,
        "Content-Type": "application/json",
    }


def mimo_chat_completions_url(api_base: str) -> str:
    return f"{_normalize_api_base(api_base)}/chat/completions"


async def transcribe_mimo_wav(
    settings: MiMoSpeechSettings,
    wav_b64: str,
    *,
    language: str = "auto",
) -> MiMoASRResult:
    """Submit one complete WAV utterance to MiMo ASR."""

    request_id = uuid.uuid4().hex
    body = {
        "model": settings.asr_model or DEFAULT_MIMO_ASR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": f"data:audio/wav;base64,{wav_b64}"},
                    }
                ],
            }
        ],
        "asr_options": {"language": language or "auto"},
    }
    timeout = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)
    logger.info(
        "voice.mimo_asr_started",
        model=body["model"],
        request_id=request_id,
        audio_b64_len=len(wav_b64),
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                mimo_chat_completions_url(settings.api_base),
                json=body,
                headers=mimo_headers(settings.api_key),
            )
    except httpx.TimeoutException as exc:
        raise MiMoAPIError("MiMo ASR request timed out", request_id=request_id) from exc
    except httpx.HTTPError as exc:
        raise MiMoAPIError("MiMo ASR connection failed", request_id=request_id) from exc

    provider_request_id = response.headers.get("x-request-id", request_id)
    if response.is_error:
        raise _response_error("MiMo ASR", response, provider_request_id)
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise MiMoAPIError(
            "MiMo ASR returned invalid JSON",
            status_code=response.status_code,
            request_id=provider_request_id,
        ) from exc

    text = _extract_completion_text(payload)
    logger.info(
        "voice.mimo_asr_completed",
        model=settings.asr_model,
        request_id=provider_request_id,
        audio_b64_len=len(wav_b64),
        text_len=len(text),
    )
    return MiMoASRResult(text=text, request_id=provider_request_id)


async def stream_mimo_tts_pcm(
    settings: MiMoSpeechSettings,
    text: str,
) -> AsyncIterator[bytes]:
    """Yield MiMo TTS's low-latency PCM16 output without buffering a reply."""

    content = text.strip()
    if not content:
        return

    messages: list[dict[str, str]] = []
    if settings.tts_style_prompt:
        messages.append({"role": "user", "content": settings.tts_style_prompt})
    messages.append({"role": "assistant", "content": content})
    body = {
        "model": settings.tts_model or DEFAULT_MIMO_TTS_MODEL,
        "messages": messages,
        "audio": {
            "format": "pcm16",
            "voice": settings.tts_voice or DEFAULT_MIMO_TTS_VOICE,
        },
        "stream": True,
    }
    request_id = uuid.uuid4().hex
    timeout = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)
    audio_bytes = 0
    logger.info(
        "voice.mimo_tts_started",
        model=body["model"],
        voice=body["audio"]["voice"],
        request_id=request_id,
        chars=len(content),
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                mimo_chat_completions_url(settings.api_base),
                json=body,
                headers=mimo_headers(settings.api_key),
            ) as response:
                provider_request_id = response.headers.get("x-request-id", request_id)
                if response.is_error:
                    error_body = await _read_error_body(response)
                    raise MiMoAPIError(
                        "MiMo TTS request failed",
                        status_code=response.status_code,
                        request_id=provider_request_id,
                        body=error_body,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("voice.mimo_tts_invalid_sse", request_id=provider_request_id)
                        continue
                    pcm = _extract_tts_pcm(event)
                    if pcm:
                        audio_bytes += len(pcm)
                        yield pcm
    except MiMoAPIError:
        raise
    except httpx.TimeoutException as exc:
        raise MiMoAPIError("MiMo TTS request timed out", request_id=request_id) from exc
    except httpx.HTTPError as exc:
        raise MiMoAPIError("MiMo TTS connection failed", request_id=request_id) from exc

    if not audio_bytes:
        raise MiMoAPIError("MiMo TTS returned no audio", request_id=request_id)
    logger.info(
        "voice.mimo_tts_completed",
        model=settings.tts_model,
        voice=settings.tts_voice,
        request_id=request_id,
        chars=len(content),
        pcm_bytes=audio_bytes,
    )


def _extract_completion_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    return ""


def _extract_tts_pcm(event: object) -> bytes:
    if not isinstance(event, dict):
        return b""
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return b""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return b""
    audio = delta.get("audio")
    if isinstance(audio, dict):
        data = audio.get("data")
    else:
        data = None
    if not isinstance(data, str) or not data:
        return b""
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        logger.warning("voice.mimo_tts_invalid_audio_base64")
        return b""


def _response_error(prefix: str, response: httpx.Response, request_id: str) -> MiMoAPIError:
    body = _safe_response_body(response)
    logger.warning(
        "voice.mimo_provider_error",
        operation=prefix,
        status_code=response.status_code,
        request_id=request_id,
        provider_error=body,
    )
    return MiMoAPIError(
        f"{prefix} request failed",
        status_code=response.status_code,
        request_id=request_id,
        body=body,
    )


async def _read_error_body(response: httpx.Response) -> object | None:
    try:
        return _safe_response_body(response, await response.aread())
    except httpx.HTTPError:
        return None


def _safe_response_body(response: httpx.Response, raw: bytes | None = None) -> object | None:
    try:
        if raw is None:
            return response.json()
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = (raw.decode("utf-8", errors="replace") if raw is not None else response.text).strip()
        return text[:300] if text else None
