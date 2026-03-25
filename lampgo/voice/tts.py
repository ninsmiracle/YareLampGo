"""TTS — Text-to-Speech via edge-tts (local, no API key needed)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class EdgeTTS:
    """Generate speech audio files using edge-tts."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    async def synthesize(self, text: str) -> Path | None:
        """Generate an MP3 file from text. Returns the file path or None on failure."""
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
        """Synthesize and play audio (blocking until playback completes)."""
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
                *cmd.split(), str(path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0:
                return
        except FileNotFoundError:
            continue
    logger.warning("tts.no_player", msg="Install ffplay or mpv to hear TTS output")
