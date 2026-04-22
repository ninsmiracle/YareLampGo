"""TTS — Text-to-Speech via MiMo-V2-TTS (streaming PCM) or edge-tts fallback."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

TTS_SAMPLE_RATE = 24000  # MiMo TTS outputs 24kHz PCM16LE mono


class MiMoTTS:
    """Streaming TTS using MiMo-V2-TTS.

    Primary mode: stream=True + pcm16 format.
    Each SSE chunk contains base64-encoded PCM16LE audio that can be
    fed directly to a sounddevice output stream for real-time playback.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.mimomimo.com/v1",
        voice: str = "mimo_default",
        style_prompt: str = "",
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._voice = voice
        self._style_prompt = style_prompt

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        """Stream PCM16LE chunks from MiMo TTS via SSE.

        Yields raw PCM bytes (24kHz, 16-bit, mono) as they arrive.
        """
        if not text.strip():
            return

        try:
            import httpx
        except ImportError:
            logger.warning("tts.no_httpx")
            return

        messages: list[dict] = []
        if self._style_prompt:
            messages.append({"role": "user", "content": self._style_prompt})
        messages.append({"role": "assistant", "content": text})

        body = {
            "model": "mimo-v2-tts",
            "messages": messages,
            "audio": {
                "format": "pcm16",
                "voice": self._voice,
            },
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

        url = f"{self._api_base}/chat/completions"
        max_retries = 2

        for attempt in range(1, max_retries + 1):
            total_bytes = 0
            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                break

                            try:
                                chunk = json.loads(payload)
                            except json.JSONDecodeError:
                                continue

                            pcm = _extract_stream_audio(chunk)
                            if pcm:
                                total_bytes += len(pcm)
                                yield pcm

                logger.debug("tts.stream_complete", chars=len(text), pcm_bytes=total_bytes)
                return
            except Exception:
                if attempt < max_retries:
                    logger.warning("tts.stream_retry", attempt=attempt, chars=len(text))
                    await asyncio.sleep(1.0)
                else:
                    logger.exception("tts.stream_failed")

    async def stream_speak(self, text: str) -> None:
        """Stream-synthesize text and play through sounddevice in real-time."""
        from lampgo.voice.audio import AudioPlayback

        player = AudioPlayback(sample_rate=TTS_SAMPLE_RATE)
        player.start()

        try:
            async for pcm_chunk in self.stream_pcm(text):
                player.feed(pcm_chunk)
            player.finish()
            await player.await_done(timeout=30.0)
        finally:
            player.stop()

    async def speak(self, text: str) -> None:
        """Synthesize and play — uses streaming if available, file fallback otherwise."""
        await self.stream_speak(text)

    async def synthesize(self, text: str) -> Path | None:
        """Non-streaming fallback: collect all PCM then save as raw file."""
        if not text.strip():
            return None

        chunks: list[bytes] = []
        async for pcm in self.stream_pcm(text):
            chunks.append(pcm)

        if not chunks:
            return None

        tmp = Path(tempfile.mktemp(suffix=".pcm"))
        tmp.write_bytes(b"".join(chunks))
        return tmp


class EdgeTTS:
    """Fallback TTS using edge-tts (free, no API key needed)."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    async def synthesize(self, text: str) -> Path | None:
        try:
            import edge_tts
        except ImportError:
            logger.warning("tts.no_edge_tts", msg="Install edge-tts: uv add edge-tts")
            return None

        if not text.strip():
            return None

        tmp = Path(tempfile.mktemp(suffix=".mp3"))
        try:
            communicate = edge_tts.Communicate(text, self._voice)
            await communicate.save(str(tmp))
            logger.debug("tts.synthesized", path=str(tmp), text=text[:30])
            return tmp
        except Exception:
            logger.exception("tts.synthesize_failed")
            return None

    async def speak(self, text: str) -> None:
        path = await self.synthesize(text)
        if path is None:
            return
        try:
            await play_audio_file(path)
        finally:
            try:
                path.unlink()
            except Exception:
                pass


async def play_audio_file(path: Path) -> None:
    """Play an audio file using system tools (ffplay, mpv, or aplay fallback)."""
    for cmd in ["ffplay -nodisp -autoexit -loglevel quiet", "mpv --no-terminal"]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd.split(),
                str(path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0:
                return
        except FileNotFoundError:
            continue
    logger.warning("tts.no_player", msg="Install ffplay or mpv to hear TTS output")


async def synthesize_for_web(
    text: str,
    api_key: str = "",
    api_base: str = "",
    voice: str = "",
    provider: str = "",
) -> tuple[str, str] | None:
    """Generate TTS audio suitable for browser playback.

    ``provider`` comes from ``config.voice.tts_provider`` and takes precedence:
      - "edge-tts" → always use EdgeTTS (no Key needed).
      - "mimo"     → use MiMoTTS (requires ``api_key``).
      - ""         → legacy auto-detect: MiMo if key is present, else Edge.

    Returns ``(base64_audio, format)`` or ``None`` on failure.
    """
    if not text.strip():
        return None

    chosen = (provider or "").strip().lower()
    if not chosen:
        chosen = "mimo" if api_key else "edge-tts"

    if chosen == "mimo":
        if not api_key:
            logger.warning(
                "tts.web_synthesize_missing_key",
                msg="tts_provider=mimo requires api_key; falling back to edge-tts",
            )
            chosen = "edge-tts"
        else:
            tts = MiMoTTS(
                api_key=api_key,
                api_base=api_base or "https://api.mimomimo.com/v1",
                voice=voice or "mimo_default",
            )
            chunks: list[bytes] = []
            async for pcm in tts.stream_pcm(text):
                chunks.append(pcm)
            if not chunks:
                return None
            wav_bytes = _pcm_to_wav(b"".join(chunks), TTS_SAMPLE_RATE)
            return base64.b64encode(wav_bytes).decode(), "wav"

    if chosen == "edge-tts":
        try:
            # Edge voice names look like "zh-CN-XiaoxiaoNeural". If `voice` is
            # something else (e.g. a mimo voice id left over from a previous
            # provider), fall back to the EdgeTTS default.
            edge_voice = voice if voice and "-" in voice else ""
            edge = EdgeTTS(voice=edge_voice) if edge_voice else EdgeTTS()
            path = await edge.synthesize(text)
            if path and path.exists():
                mp3_bytes = path.read_bytes()
                path.unlink(missing_ok=True)
                return base64.b64encode(mp3_bytes).decode(), "mp3"
        except Exception:
            logger.exception("tts.web_synthesize_failed")
        return None

    logger.warning("tts.web_synthesize_unknown_provider", provider=chosen)
    return None


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM16LE mono data in a WAV header."""
    import struct

    data_len = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_len,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_len,
    )
    return header + pcm


def _extract_stream_audio(chunk: dict) -> bytes | None:
    """Extract PCM bytes from a streaming SSE chunk (delta.audio.data)."""
    choices = chunk.get("choices", [])
    if not choices:
        return None
    delta = choices[0].get("delta", {})
    audio = delta.get("audio")
    if isinstance(audio, dict):
        b64 = audio.get("data", "")
        if b64:
            return base64.b64decode(b64)
    return None
