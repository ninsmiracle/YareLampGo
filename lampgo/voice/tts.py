"""TTS — Text-to-Speech via Volcengine streaming TTS or edge-tts fallback."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import tempfile
import uuid
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

TTS_SAMPLE_RATE = 24000
DEFAULT_VOLCENGINE_TTS_VOICE = "zh_female_vv_uranus_bigtts"
VOLCENGINE_TTS_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
VOLCENGINE_TTS_RECEIVE_TIMEOUT_S = 20.0
VOLCENGINE_SEED_TTS_2_RESOURCE_ID = "seed-tts-2.0"
VOLCENGINE_SEED_TTS_1_RESOURCE_ID = "seed-tts-1.0"
VOLCENGINE_SEED_ICL_2_RESOURCE_ID = "seed-icl-2.0"
VOLCENGINE_BIGTTS_RESOURCE_ID = "volc.service_type.10029"

VOLCENGINE_TTS_VOICE_ALIASES: dict[str, str] = {
    # Keep older saved settings working with the currently granted Seed-TTS 2
    # voice family. The mars/moon variants either consume a different quota
    # pool or fail with the current app credentials.
    "zh_male_lubanqihao_mars_bigtts": "zh_male_lubanqihao_uranus_bigtts",
    "zh_male_dongmanhaimian_mars_bigtts": "zh_male_liangsangmengzai_uranus_bigtts",
    "zh_male_wennuanahu_moon_bigtts": "zh_male_wennuanahu_uranus_bigtts",
}


class MsgType(IntEnum):
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_CLIENT = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    AUDIO_ONLY_SERVER = 0b1011
    ERROR = 0b1111


class MsgFlag(IntEnum):
    NO_SEQ = 0b0000
    POSITIVE_SEQ = 0b0001
    NEGATIVE_SEQ = 0b0011
    WITH_EVENT = 0b0100


class Serialization(IntEnum):
    RAW = 0
    JSON = 1


class EventType(IntEnum):
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    START_SESSION = 100
    CANCEL_SESSION = 101
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_CANCELED = 151
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    TASK_REQUEST = 200


@dataclass
class VolcMessage:
    msg_type: MsgType
    flag: MsgFlag = MsgFlag.NO_SEQ
    event: EventType | int = 0
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""
    serialization: Serialization = Serialization.JSON

    def marshal(self) -> bytes:
        header_size = 1
        header = bytes(
            [
                (1 << 4) | header_size,
                (int(self.msg_type) << 4) | int(self.flag),
                (int(self.serialization) << 4),
                0,
            ]
        )
        out = io.BytesIO()
        out.write(header)

        if self.flag == MsgFlag.WITH_EVENT:
            out.write(struct.pack(">i", int(self.event)))
            if int(self.event) not in {
                EventType.START_CONNECTION,
                EventType.FINISH_CONNECTION,
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
            }:
                session = self.session_id.encode("utf-8")
                out.write(struct.pack(">I", len(session)))
                out.write(session)

        if self.msg_type in {
            MsgType.FULL_CLIENT_REQUEST,
            MsgType.FULL_SERVER_RESPONSE,
            MsgType.AUDIO_ONLY_CLIENT,
            MsgType.AUDIO_ONLY_SERVER,
        } and self.flag in {MsgFlag.POSITIVE_SEQ, MsgFlag.NEGATIVE_SEQ}:
            out.write(struct.pack(">i", self.sequence))
        elif self.msg_type == MsgType.ERROR:
            out.write(struct.pack(">I", self.error_code))

        out.write(struct.pack(">I", len(self.payload)))
        out.write(self.payload)
        return out.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> VolcMessage:
        if len(data) < 4:
            raise ValueError(f"Volcengine message too short: {len(data)}")

        header_size = data[0] & 0x0F
        msg_type = MsgType(data[1] >> 4)
        flag = MsgFlag(data[1] & 0x0F)
        serialization = Serialization(data[2] >> 4)
        offset = header_size * 4

        event: EventType | int = 0
        session_id = ""
        connect_id = ""
        sequence = 0
        error_code = 0

        if msg_type in {
            MsgType.FULL_CLIENT_REQUEST,
            MsgType.FULL_SERVER_RESPONSE,
            MsgType.AUDIO_ONLY_CLIENT,
            MsgType.AUDIO_ONLY_SERVER,
        } and flag in {MsgFlag.POSITIVE_SEQ, MsgFlag.NEGATIVE_SEQ}:
            sequence = struct.unpack(">i", data[offset : offset + 4])[0]
            offset += 4
        elif msg_type == MsgType.ERROR:
            error_code = struct.unpack(">I", data[offset : offset + 4])[0]
            offset += 4

        if flag == MsgFlag.WITH_EVENT:
            raw_event = struct.unpack(">i", data[offset : offset + 4])[0]
            offset += 4
            try:
                event = EventType(raw_event)
            except ValueError:
                event = raw_event

            if int(event) not in {
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
                EventType.CONNECTION_FINISHED,
            }:
                session_size = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                if session_size:
                    session_id = data[offset : offset + session_size].decode("utf-8", errors="ignore")
                    offset += session_size

            if int(event) in {
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
                EventType.CONNECTION_FINISHED,
            } and offset + 4 <= len(data):
                connect_size = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                if connect_size:
                    connect_id = data[offset : offset + connect_size].decode("utf-8", errors="ignore")
                    offset += connect_size

        if offset + 4 > len(data):
            payload = b""
        else:
            payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
            offset += 4
            payload = data[offset : offset + payload_size] if payload_size else b""

        return cls(
            msg_type=msg_type,
            flag=flag,
            event=event,
            session_id=session_id,
            connect_id=connect_id,
            sequence=sequence,
            error_code=error_code,
            payload=payload,
            serialization=serialization,
        )


class VolcengineTTS:
    """Streaming TTS using Volcengine V3 bidirectional WebSocket.

    Audio is requested as raw PCM16LE mono at 24 kHz so local playback and web
    streaming can start as soon as the first audio frame arrives.
    """

    def __init__(
        self,
        app_id: str,
        access_token: str,
        voice: str = DEFAULT_VOLCENGINE_TTS_VOICE,
        model: str = "",
        endpoint: str = VOLCENGINE_TTS_ENDPOINT,
        sample_rate: int = TTS_SAMPLE_RATE,
    ) -> None:
        self._app_id = app_id.strip()
        self._access_token = access_token.strip()
        self._voice = _volcengine_voice_or_default(voice)
        self._model = (model or "").strip()
        self._endpoint = endpoint
        self._sample_rate = sample_rate

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        """Stream raw PCM16LE chunks from Volcengine TTS."""
        if not text.strip():
            return
        if not self._app_id or not self._access_token:
            logger.warning("tts.volcengine_missing_credentials")
            return

        try:
            import websockets
        except ImportError:
            logger.warning("tts.no_websockets")
            return

        request_id = str(uuid.uuid4())
        logger.info("tts.volcengine_connecting", request_id=request_id, voice=self._voice)

        websocket = None
        try:
            websocket = await websockets.connect(
                self._endpoint,
                additional_headers=self._build_auth_headers(request_id),
                max_size=10 * 1024 * 1024,
                open_timeout=10,
                close_timeout=5,
            )
            await _send_start_connection(websocket)
            await _wait_for_event(websocket, EventType.CONNECTION_STARTED)

            session_id = str(uuid.uuid4())
            await _send_start_session(
                websocket,
                session_id=session_id,
                payload=self._build_session_payload(EventType.START_SESSION),
            )
            await _wait_for_event(websocket, EventType.SESSION_STARTED)

            send_task = asyncio.create_task(self._send_text(websocket, session_id, text))
            audio_bytes = 0
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            _receive_message(websocket),
                            timeout=VOLCENGINE_TTS_RECEIVE_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError as exc:
                        raise RuntimeError(
                            f"volcengine TTS receive timeout after {VOLCENGINE_TTS_RECEIVE_TIMEOUT_S:.0f}s"
                        ) from exc
                    if msg.msg_type == MsgType.AUDIO_ONLY_SERVER:
                        if msg.payload:
                            audio_bytes += len(msg.payload)
                            yield msg.payload
                        continue
                    if msg.msg_type == MsgType.ERROR:
                        raise RuntimeError(_format_volc_error(msg))
                    if msg.msg_type == MsgType.FULL_SERVER_RESPONSE:
                        if msg.event == EventType.SESSION_FINISHED:
                            break
                        if msg.event in {
                            EventType.SESSION_FAILED,
                            EventType.CONNECTION_FAILED,
                        }:
                            raise RuntimeError(_format_volc_error(msg))
                await send_task
            finally:
                if not send_task.done():
                    send_task.cancel()
                    await asyncio.gather(send_task, return_exceptions=True)

            logger.debug(
                "tts.volcengine_stream_complete",
                request_id=request_id,
                chars=len(text),
                pcm_bytes=audio_bytes,
            )
        except RuntimeError as exc:
            logger.error(
                "tts.volcengine_stream_failed",
                request_id=request_id,
                voice=self._voice,
                error=str(exc),
            )
            return
        except Exception:
            logger.exception("tts.volcengine_stream_failed", request_id=request_id, voice=self._voice)
            return
        finally:
            if websocket is not None:
                try:
                    await _send_finish_connection(websocket)
                    await _wait_for_event(websocket, EventType.CONNECTION_FINISHED)
                except Exception:
                    pass
                try:
                    await websocket.close()
                except Exception:
                    pass

    async def _send_text(self, websocket, session_id: str, text: str) -> None:
        for chunk in _split_text_for_tts(text):
            payload = self._build_session_payload(EventType.TASK_REQUEST, text=chunk)
            await _send_task_request(websocket, session_id=session_id, payload=payload)
            await asyncio.sleep(0.005)
        await _send_finish_session(websocket, session_id=session_id)

    def _build_auth_headers(self, request_id: str) -> dict[str, str]:
        return {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": _resource_id_for_voice(self._voice),
            "X-Api-Connect-Id": request_id,
        }

    def _build_session_payload(self, event: EventType, text: str = "") -> bytes:
        req_params: dict = {
            "speaker": self._voice,
            "audio_params": {
                "format": "pcm",
                "sample_rate": self._sample_rate,
                "enable_timestamp": False,
            },
            "additions": json.dumps({"disable_markdown_filter": False}, ensure_ascii=False),
        }
        if text:
            req_params["text"] = text
        if self._model and self._model.startswith("seed-tts-"):
            req_params["model"] = self._model

        payload = {
            "user": {"uid": self._app_id},
            "namespace": "BidirectionalTTS",
            "event": int(event),
            "req_params": req_params,
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def stream_speak(self, text: str) -> None:
        """Stream-synthesize text and play through sounddevice in real time."""
        from lampgo.voice.audio import AudioPlayback

        player = AudioPlayback(sample_rate=self._sample_rate)
        player.start()
        try:
            async for pcm_chunk in self.stream_pcm(text):
                player.feed(pcm_chunk)
            player.finish()
            await player.await_done(timeout=30.0)
        finally:
            player.stop()

    async def speak(self, text: str) -> None:
        await self.stream_speak(text)

    async def synthesize(self, text: str) -> Path | None:
        """Collect a streaming synthesis into a temporary WAV file."""
        if not text.strip():
            return None

        chunks: list[bytes] = []
        async for pcm in self.stream_pcm(text):
            chunks.append(pcm)

        if not chunks:
            return None

        tmp = Path(tempfile.mktemp(suffix=".wav"))
        tmp.write_bytes(_pcm_to_wav(b"".join(chunks), self._sample_rate))
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
            logger.debug("tts.edge_synthesized", path=str(tmp), text=text[:30])
            return tmp
        except Exception:
            logger.exception("tts.edge_synthesize_failed")
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


async def iter_synthesize_for_web(
    text: str,
    app_id: str = "",
    access_token: str = "",
    voice: str = "",
    provider: str = "",
    model: str = "",
) -> AsyncIterator[tuple[str, str, int]]:
    """Yield TTS audio chunks suitable for browser playback.

    Yields ``(base64_audio, format, sample_rate)``. Volcengine emits raw
    ``pcm16`` chunks progressively; edge-tts emits one mp3 buffer.
    """
    if not text.strip():
        return

    chosen = _choose_provider(provider, app_id, access_token)

    if chosen == "volcengine":
        tts = VolcengineTTS(
            app_id=app_id,
            access_token=access_token,
            voice=voice or DEFAULT_VOLCENGINE_TTS_VOICE,
            model=model,
        )
        yielded = False
        async for pcm in tts.stream_pcm(text):
            if not pcm:
                continue
            yielded = True
            yield base64.b64encode(pcm).decode("ascii"), "pcm16", tts.sample_rate
        if yielded:
            return

        logger.warning("tts.web_volcengine_empty_falling_back_edge")
        chosen = "edge-tts"

    if chosen == "edge-tts":
        try:
            edge_voice = _edge_voice_or_default(voice)
            edge = EdgeTTS(voice=edge_voice)
            path = await edge.synthesize(text)
            if path and path.exists():
                mp3_bytes = path.read_bytes()
                path.unlink(missing_ok=True)
                yield base64.b64encode(mp3_bytes).decode("ascii"), "mp3", 0
        except Exception:
            logger.exception("tts.web_edge_synthesize_failed")


async def synthesize_for_web(
    text: str,
    app_id: str = "",
    access_token: str = "",
    voice: str = "",
    provider: str = "",
    model: str = "",
) -> tuple[str, str] | None:
    """Compatibility wrapper that collects web TTS into a single buffer.

    New playback paths should use :func:`iter_synthesize_for_web` so Volcengine
    audio can be played while it is still being synthesized.
    """
    chunks: list[bytes] = []
    final_format = ""
    final_rate = 0
    async for audio_b64, fmt, sample_rate in iter_synthesize_for_web(
        text,
        app_id=app_id,
        access_token=access_token,
        voice=voice,
        provider=provider,
        model=model,
    ):
        chunks.append(base64.b64decode(audio_b64))
        final_format = fmt
        final_rate = sample_rate

    if not chunks:
        return None
    if final_format == "pcm16":
        return base64.b64encode(_pcm_to_wav(b"".join(chunks), final_rate or TTS_SAMPLE_RATE)).decode(), "wav"
    return base64.b64encode(b"".join(chunks)).decode(), final_format or "mp3"


def _choose_provider(provider: str, app_id: str, access_token: str) -> str:
    chosen = (provider or "").strip().lower()
    if chosen in {"", "auto"}:
        return "volcengine" if app_id and access_token else "edge-tts"
    if chosen in {"volc", "volcano", "huoshan", "volcengine-tts"}:
        chosen = "volcengine"
    if chosen == "mimo":
        logger.warning("tts.provider_mimo_removed_using_volcengine")
        chosen = "volcengine"
    if chosen == "volcengine" and not (app_id and access_token):
        logger.warning("tts.volcengine_missing_credentials_falling_back_edge")
        return "edge-tts"
    if chosen != "edge-tts" and chosen != "volcengine":
        logger.warning("tts.unknown_provider_falling_back_edge", provider=chosen)
        return "edge-tts"
    return chosen


def _edge_voice_or_default(voice: str) -> str:
    voice = (voice or "").strip()
    return voice if "-" in voice and voice.endswith("Neural") else "zh-CN-XiaoxiaoNeural"


def _volcengine_voice_or_default(voice: str) -> str:
    voice = (voice or "").strip()
    if not voice or voice == "mimo_default" or (voice.endswith("Neural") and "-" in voice):
        return DEFAULT_VOLCENGINE_TTS_VOICE
    return VOLCENGINE_TTS_VOICE_ALIASES.get(voice, voice)


def _resource_id_for_voice(voice: str) -> str:
    voice = (voice or "").strip()
    if voice.startswith("S_"):
        return VOLCENGINE_SEED_ICL_2_RESOURCE_ID
    if voice.startswith("saturn_") or "_uranus_bigtts" in voice:
        return VOLCENGINE_SEED_TTS_2_RESOURCE_ID
    if "_moon_bigtts" in voice:
        return VOLCENGINE_SEED_TTS_1_RESOURCE_ID
    return VOLCENGINE_BIGTTS_RESOURCE_ID


def _split_text_for_tts(text: str, max_chars: int = 30) -> list[str]:
    chunks: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in "。！？!?；;\n" or len(current) >= max_chars:
            stripped = current.strip()
            if stripped:
                chunks.append(stripped)
            current = ""
    stripped = current.strip()
    if stripped:
        chunks.append(stripped)
    return chunks


async def _send_start_connection(websocket) -> None:
    msg = VolcMessage(
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgFlag.WITH_EVENT,
        event=EventType.START_CONNECTION,
        payload=b"{}",
    )
    await websocket.send(msg.marshal())


async def _send_finish_connection(websocket) -> None:
    msg = VolcMessage(
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgFlag.WITH_EVENT,
        event=EventType.FINISH_CONNECTION,
        payload=b"{}",
    )
    await websocket.send(msg.marshal())


async def _send_start_session(websocket, session_id: str, payload: bytes) -> None:
    msg = VolcMessage(
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgFlag.WITH_EVENT,
        event=EventType.START_SESSION,
        session_id=session_id,
        payload=payload,
    )
    await websocket.send(msg.marshal())


async def _send_finish_session(websocket, session_id: str) -> None:
    msg = VolcMessage(
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgFlag.WITH_EVENT,
        event=EventType.FINISH_SESSION,
        session_id=session_id,
        payload=b"{}",
    )
    await websocket.send(msg.marshal())


async def _send_task_request(websocket, session_id: str, payload: bytes) -> None:
    msg = VolcMessage(
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgFlag.WITH_EVENT,
        event=EventType.TASK_REQUEST,
        session_id=session_id,
        payload=payload,
    )
    await websocket.send(msg.marshal())


async def _receive_message(websocket) -> VolcMessage:
    data = await websocket.recv()
    if isinstance(data, str):
        raise RuntimeError(f"unexpected Volcengine text message: {data[:200]}")
    return VolcMessage.from_bytes(data)


async def _wait_for_event(websocket, event: EventType) -> VolcMessage:
    msg = await _receive_message(websocket)
    if msg.msg_type == MsgType.ERROR:
        raise RuntimeError(_format_volc_error(msg))
    if msg.event != event:
        raise RuntimeError(f"unexpected Volcengine event: got={msg.event!r} want={event!r}")
    return msg


def _format_volc_error(msg: VolcMessage) -> str:
    payload = msg.payload.decode("utf-8", errors="ignore")
    return f"Volcengine TTS error event={msg.event!r} code={msg.error_code} payload={payload[:300]}"


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM16LE mono data in a WAV header."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out.getvalue()
