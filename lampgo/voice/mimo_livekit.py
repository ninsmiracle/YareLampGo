"""MiMo speech adapters for the managed LiveKit Agent SDK.

The published SDK currently exposes Volcengine-only STT/TTS factories.  The
backend injects this small compatibility patch before the SDK process imports
its worker, keeping RTC transport in LiveKit while speech requests stay on
LampGo's configured MiMo endpoint.
"""

from __future__ import annotations

import uuid
from typing import Any

from lampgo.voice.mimo import (
    MIMO_TTS_SAMPLE_RATE,
    MiMoAPIError,
    MiMoSpeechSettings,
    stream_mimo_tts_pcm,
    transcribe_mimo_wav,
)


def install_livekit_agent_sdk_mimo_patch() -> None:
    """Enable ``type: openai`` for SDK STT/TTS and replace its factories."""

    from lampgo_livekit_agent import config as sdk_config
    from lampgo_livekit_agent import speech as sdk_speech
    from lampgo_livekit_agent import worker as sdk_worker

    sdk_config._SUPPORTED_COMPONENT_TYPES["stt"].add("openai")
    sdk_config._SUPPORTED_COMPONENT_TYPES["tts"].add("openai")
    sdk_speech.create_stt = create_stt
    sdk_speech.create_tts = create_tts
    # ``worker.py`` imports these functions by name during module import, so
    # changing only ``speech.create_*`` leaves an already-imported worker bound
    # to the SDK's Volcengine-only factories. Patch both module references.
    sdk_worker.create_stt = create_stt
    sdk_worker.create_tts = create_tts


def create_stt(*, config, runtime) -> Any:
    component = _require_component(runtime.voice_agent.stt, "stt")
    provider = config.provider_for(component, path=f"voice_agents.{runtime.voice_agent.name}.stt")
    return MiMoLiveKitSTT(
        settings=_speech_settings(provider, component.options),
        language=str(component.options.get("language") or "auto"),
    )


def create_tts(*, config, runtime) -> Any:
    component = _require_component(runtime.voice_agent.tts, "tts")
    provider = config.provider_for(component, path=f"voice_agents.{runtime.voice_agent.name}.tts")
    return MiMoLiveKitTTS(settings=_speech_settings(provider, component.options))


def _require_component(component, name: str):
    if component is None:
        raise RuntimeError(f"{name} component is not configured")
    return component


def _speech_settings(provider, options: dict[str, Any]) -> MiMoSpeechSettings:
    if provider.type != "openai":
        raise RuntimeError(f"MiMo speech requires an OpenAI-compatible provider, got {provider.type!r}")
    api_key = str(provider.get_extra("api_key") or "").strip()
    base_url = str(provider.get_extra("base_url") or "").strip()
    if not api_key or not base_url:
        raise RuntimeError("MiMo speech provider requires api_key and base_url")
    return MiMoSpeechSettings(
        api_base=base_url,
        api_key=api_key,
        asr_model=str(options.get("asr_model") or options.get("model") or "mimo-v2.5-asr"),
        tts_model=str(options.get("tts_model") or options.get("model") or "mimo-v2.5-tts"),
        tts_voice=str(options.get("voice") or "mimo_default"),
        tts_style_prompt=str(options.get("style_prompt") or ""),
    )


class MiMoLiveKitSTT:
    """Late-bound subclass factory avoids importing LiveKit outside the SDK."""

    def __new__(cls, *, settings: MiMoSpeechSettings, language: str):
        from livekit.agents import stt
        from livekit.agents.types import NOT_GIVEN
        from livekit import rtc

        class _MiMoLiveKitSTT(stt.STT):
            def __init__(self) -> None:
                super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
                self._settings = settings
                self._language = language

            @property
            def model(self) -> str:
                return self._settings.asr_model

            @property
            def provider(self) -> str:
                return "mimo"

            async def _recognize_impl(self, buffer, *, language=NOT_GIVEN, conn_options):
                requested_language = self._language
                if language is not NOT_GIVEN and language:
                    requested_language = str(language)
                wav_b64 = __import__("base64").b64encode(
                    rtc.combine_audio_frames(buffer).to_wav_bytes()
                ).decode("ascii")
                try:
                    result = await transcribe_mimo_wav(
                        self._settings,
                        wav_b64,
                        language=requested_language,
                    )
                except MiMoAPIError as exc:
                    from livekit.agents import APIStatusError

                    raise APIStatusError(
                        str(exc),
                        status_code=exc.status_code,
                        request_id=exc.request_id or None,
                        body=exc.body,
                    ) from exc
                return stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=result.request_id,
                    alternatives=[stt.SpeechData(text=result.text, language="zh")],
                )

        return _MiMoLiveKitSTT()


class MiMoLiveKitTTS:
    """Late-bound TTS adapter that streams MiMo PCM16 into LiveKit frames."""

    def __new__(cls, *, settings: MiMoSpeechSettings):
        from livekit.agents import tts
        from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

        class _MiMoChunkedStream(tts.ChunkedStream):
            async def _run(self, output_emitter) -> None:
                initialized = False
                request_id = uuid.uuid4().hex
                try:
                    async for pcm in stream_mimo_tts_pcm(settings, self.input_text):
                        if not initialized:
                            output_emitter.initialize(
                                request_id=request_id,
                                sample_rate=MIMO_TTS_SAMPLE_RATE,
                                num_channels=1,
                                mime_type="audio/pcm",
                            )
                            initialized = True
                        output_emitter.push(pcm)
                except MiMoAPIError as exc:
                    from livekit.agents import APIStatusError

                    raise APIStatusError(
                        str(exc),
                        status_code=exc.status_code,
                        request_id=exc.request_id or None,
                        body=exc.body,
                    ) from exc
                if initialized:
                    output_emitter.flush()

        class _MiMoLiveKitTTS(tts.TTS):
            def __init__(self) -> None:
                super().__init__(
                    capabilities=tts.TTSCapabilities(streaming=False),
                    sample_rate=MIMO_TTS_SAMPLE_RATE,
                    num_channels=1,
                )
                self._settings = settings

            @property
            def model(self) -> str:
                return self._settings.tts_model

            @property
            def provider(self) -> str:
                return "mimo"

            def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
                return _MiMoChunkedStream(tts=self, input_text=text, conn_options=conn_options)

        return _MiMoLiveKitTTS()
