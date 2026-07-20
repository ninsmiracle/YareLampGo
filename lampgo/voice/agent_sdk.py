"""Lampgo LiveKit Agent SDK subprocess manager.

Generates ``roles.yaml`` from lampgo's VoiceConfig at runtime and
manages the ``lampgo-livekit-agent-sdk`` child process lifecycle.
The Agent SDK process connects to the LiveKit server, subscribes to
audio in the room, runs ASR/TTS via Volcengine, and calls lampgo's
``/v1/chat/completions`` endpoint for LLM responses.
"""

from __future__ import annotations

import asyncio
import getpass
import importlib.util
import json
import os
import shutil
import signal
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog

if TYPE_CHECKING:
    from lampgo.core.config import VoiceConfig

logger = structlog.get_logger(__name__)

ROLES_YAML_TEMPLATE = """\
version: 1

livekit:
  url: {livekit_url}

platform:
  rtc_token_endpoint: {rtc_token_endpoint}
  agent_token_endpoint: {agent_token_endpoint}
  rtc_token_api_key: {rtc_token_api_key}

agent:
  name_prefix: lampgo-agent
  registration_token: {agent_registration_token}

providers:
  lampgo_llm:
    type: openai
    api_key: lampgo-local
    base_url: http://127.0.0.1:{web_port}/v1

  volc_main:
    type: volcengine
    app_id: {volcengine_app_id}
    access_token: {volcengine_access_token}

defaults:
  stt:
    provider: volc_main
    options:
      resource_id: volc.bigasr.sauc.duration
      sample_rate: 16000
      result_type: single

  llm:
    provider: lampgo_llm
    options:
      model: lampgo
      temperature: 0.3
      max_tokens: 512

  tts:
    provider: volc_main
    options:
      cluster: volcano_tts
      voice: {tts_voice}
      sample_rate: 24000
      streaming: true

voice_agents:
  - name: lampgo-jarvis
    display_name: "desk lamp voice assistant"
    llm:
      system_prompt: >-
        You are the desk lamp's voice interface. Preserve the backend persona
        identity and reply in the same language as the user. Keep answers short,
        no Markdown or emoji.
"""


AGENT_SDK_PORT = 18790
AGENT_SDK_PACKAGE = "lampgo-livekit-agent-sdk"
AGENT_SDK_MODULE = "lampgo_livekit_agent"
AGENT_SDK_BINARIES = ("lampgo-livekit-agent",)
DEFAULT_LAMPGO_LIVEKIT_URL = "https://rtc.yhaox.top"
DEFAULT_LAMPGO_RTC_TOKEN_API_KEY = "livekit-token"
DEFAULT_LAMPGO_AGENT_REGISTRATION_TOKEN = "livekit-token"

_LOCAL_NO_PROXY_HOSTS = (
    "127.0.0.1",
    "localhost",
    "::1",
    "0.0.0.0",
    ".local",
    "192.168.0.0/16",
    "10.0.0.0/8",
    "172.16.0.0/12",
)

_BIND_FAILURE_MARKERS = (
    "address already in use",
    "errno 48",
    "errno 98",
    "winerror 10048",
)


@dataclass(frozen=True)
class _SDKPortOwner:
    """A verified LampGo SDK process group that owns the local SDK port."""

    listener_pid: int
    root_pid: int
    process_group_id: int | None
    process_name: str


def _command_runs_agent_sdk(command: list[str]) -> bool:
    """Return whether an argv contains the exact LampGo SDK executable."""
    expected = {name.casefold() for name in AGENT_SDK_BINARIES}
    for token in command:
        # ``Path`` follows the host OS and therefore does not split a Windows
        # path when tests or diagnostics run on Unix (and vice versa).
        executable = str(token).replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable in expected:
            return True
    return False


def _yaml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _livekit_ws_url(value: str) -> str:
    raw = (value or DEFAULT_LAMPGO_LIVEKIT_URL).strip()
    if raw.startswith("https://"):
        return "wss://" + raw.removeprefix("https://")
    if raw.startswith("http://"):
        return "ws://" + raw.removeprefix("http://")
    return raw


def _livekit_http_url(value: str) -> str:
    raw = (value or DEFAULT_LAMPGO_LIVEKIT_URL).strip()
    if raw.startswith("wss://"):
        return "https://" + raw.removeprefix("wss://")
    if raw.startswith("ws://"):
        return "http://" + raw.removeprefix("ws://")
    return raw

# ``sitecustomize.py`` is auto-imported by every Python interpreter that has
# the containing directory on ``sys.path``.  By staging this file in a
# dedicated dir and prepending it to ``PYTHONPATH`` before launching the SDK
# binary, we guarantee the patch is applied to:
#   1. The SDK's main process (token API).
#   2. Every multiprocessing-spawned worker process (LiveKit agent worker).
#
# This is essential because the LiveKit Agent SDK uses ``multiprocessing.spawn``
# to fork workers, and a ``python -c`` launcher's monkey-patches do NOT
# propagate to those workers (the child interpreter starts fresh and reimports
# everything).  ``sitecustomize.py`` does propagate.
#
# The patch itself:
#   1. Clamp ``AudioEmitter.num_segments`` to ``min(_num_segments, 1)`` so the
#      livekit-agents TTS post-flight validation passes when the Volcengine
#      SentenceTokenizer splits one push into multiple segments.
#   2. Convert lampgo's private OpenAI SSE flush marker into LiveKit's internal
#      ``FlushSentinel`` after the OpenAI LLM stream has passed LiveKit's metrics
#      monitor. OpenAI Chat Completions has no standard "flush this TTS segment
#      now" event, but LiveKit's TTS pipeline does. This lets each lampgo ``say``
#      narration start TTS before the final response completes.
_SITECUSTOMIZE_CODE = """\
import os
import sys
import logging

# Route every Python process started under our PYTHONPATH (token API + every
# multiprocessing-spawned worker) into a single stderr stream with PID labels.
# Without this, livekit-agents' INFO/WARNING logs about worker registration,
# job dispatch, ASR/TTS lifecycle, etc. are silently dropped — making voice
# bugs invisible.
logging.basicConfig(
    level=os.environ.get("LAMPGO_SDK_LOG", "INFO"),
    format="[%(levelname)s][%(name)s][pid=%(process)d] %(message)s",
    stream=sys.stderr,
    force=True,
)

try:
    from livekit.agents.tts.tts import AudioEmitter

    @property
    def _lampgo_clamped_num_segments(self):
        return min(self._num_segments, 1)

    AudioEmitter.num_segments = _lampgo_clamped_num_segments
    print("[lampgo] patched TTS segment check (pid=%d)" % os.getpid(),
          file=sys.stderr, flush=True)
except Exception as e:
    print("[lampgo] TTS patch failed: %r" % (e,), file=sys.stderr, flush=True)

try:
    from livekit.agents.voice.agent import Agent as _LampgoLiveKitAgent
    from livekit.agents.types import FlushSentinel

    _LAMPGO_TTS_FLUSH_MARKER = "\\ue000LAMPGO_TTS_FLUSH\\ue000"
    _orig_llm_node = _LampgoLiveKitAgent.default.llm_node

    async def _lampgo_llm_node_with_tts_flush(agent, chat_ctx, tools, model_settings):
        async for chunk in _orig_llm_node(agent, chat_ctx, tools, model_settings):
            delta = getattr(chunk, "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if isinstance(content, str) and content.strip() == _LAMPGO_TTS_FLUSH_MARKER:
                yield FlushSentinel()
                continue
            yield chunk

    _LampgoLiveKitAgent.default.llm_node = staticmethod(_lampgo_llm_node_with_tts_flush)
    print("[lampgo] patched OpenAI stream TTS flush marker (pid=%d)" % os.getpid(),
          file=sys.stderr, flush=True)
except Exception as e:
    print("[lampgo] TTS flush patch failed: %r" % (e,), file=sys.stderr, flush=True)

try:
    from livekit.agents.voice.agent_session import AgentSession as _LampgoAgentSession
    from livekit.agents.types import NOT_GIVEN as _LAMPGO_NOT_GIVEN

    _orig_agent_session_init = _LampgoAgentSession.__init__
    _LAMPGO_ALLOW_INTERRUPTIONS = os.environ.get("LAMPGO_LIVEKIT_ALLOW_INTERRUPTIONS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    def _lampgo_agent_session_init(self, *args, **kwargs):
        # Enable RTC barge-in by default: when the user speaks over the
        # assistant, LiveKit can interrupt the current TTS/LLM turn and start
        # listening again. Keep a small word threshold so speaker echo or a
        # single noise burst does not instantly cancel playback.
        if "turn_handling" not in kwargs or kwargs.get("turn_handling") is _LAMPGO_NOT_GIVEN:
            kwargs.setdefault("allow_interruptions", _LAMPGO_ALLOW_INTERRUPTIONS)
            kwargs.setdefault("min_interruption_words", 3)
            kwargs["aec_warmup_duration"] = max(float(kwargs.get("aec_warmup_duration", 3.0) or 0.0), 8.0)
        return _orig_agent_session_init(self, *args, **kwargs)

    _LampgoAgentSession.__init__ = _lampgo_agent_session_init
    print("[lampgo] patched AgentSession interruption defaults (pid=%d)" % os.getpid(),
          file=sys.stderr, flush=True)
except Exception as e:
    print("[lampgo] AgentSession interruption patch failed: %r" % (e,), file=sys.stderr, flush=True)

try:
    import asyncio
    import aiohttp
    from livekit import rtc
    from livekit.agents import (
        APIError,
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        stt as _lk_stt,
        utils,
    )
    from livekit.plugins.volcengine import _protocol
    from livekit.plugins.volcengine import stt as _volc_stt
    from livekit.plugins.volcengine._utils import make_request_id, map_asr_error

    _LAMPGO_ASR_KEEPALIVE_S = float(os.environ.get("LAMPGO_VOLCENGINE_ASR_KEEPALIVE_S", "1.0"))

    async def _lampgo_volc_speech_stream_run(self):
        request_id = make_request_id()
        url = f"{self._opts.base_url}/{self._opts.endpoint}"
        headers = {
            "X-Api-App-Key": self._app_key,
            "X-Api-Access-Key": self._access_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": request_id,
        }

        try:
            ws = await asyncio.wait_for(
                self._session.ws_connect(url, headers=headers),
                self._conn_options.timeout,
            )
        except asyncio.TimeoutError as e:
            raise APITimeoutError("volcengine ASR connect timeout") from e
        except aiohttp.ClientResponseError as e:
            raise APIStatusError(
                message=e.message,
                status_code=e.status,
                request_id=request_id,
                body=None,
            ) from e
        except Exception as e:
            raise APIConnectionError("failed to connect to volcengine ASR") from e

        send_done = asyncio.Event()
        recv_error = []

        @utils.log_exceptions(logger=_volc_stt.logger)
        async def send_task():
            try:
                payload = _volc_stt._build_request_payload(self._opts, request_id=request_id)
                await ws.send_bytes(_protocol.build_full_client_request(payload))

                samples_200ms = self._opts.sample_rate // 5
                audio_bstream = utils.audio.AudioByteStream(
                    sample_rate=self._opts.sample_rate,
                    num_channels=self._opts.num_channels,
                    samples_per_channel=samples_200ms,
                )
                silence = b"\\x00\\x00" * samples_200ms * self._opts.num_channels
                sent_last = False

                while True:
                    try:
                        data = await asyncio.wait_for(
                            self._input_ch.recv(),
                            timeout=max(_LAMPGO_ASR_KEEPALIVE_S, 0.2),
                        )
                    except asyncio.TimeoutError:
                        if not sent_last:
                            await ws.send_bytes(_protocol.build_audio_only_request(silence))
                        continue
                    except utils.aio.ChanClosed:
                        break

                    if sent_last:
                        continue

                    frames = []
                    flush = False
                    if isinstance(data, rtc.AudioFrame):
                        frames.extend(audio_bstream.write(data.data.tobytes()))
                    elif isinstance(data, self._FlushSentinel):
                        frames.extend(audio_bstream.flush())
                        flush = True

                    for frame in frames:
                        await ws.send_bytes(
                            _protocol.build_audio_only_request(frame.data.tobytes())
                        )

                    if flush:
                        await ws.send_bytes(_protocol.build_audio_only_request(b"", last=True))
                        sent_last = True

                if not sent_last:
                    await ws.send_bytes(_protocol.build_audio_only_request(b"", last=True))
            finally:
                send_done.set()

        @utils.log_exceptions(logger=_volc_stt.logger)
        async def recv_task():
            try:
                while True:
                    msg = await ws.receive()
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                    ):
                        if not send_done.is_set():
                            raise APIStatusError(
                                message="volcengine ASR connection closed unexpectedly",
                                status_code=ws.close_code or -1,
                                request_id=request_id,
                                body=None,
                            )
                        return

                    if msg.type != aiohttp.WSMsgType.BINARY:
                        _volc_stt.logger.warning("unexpected volcengine ASR message type %s", msg.type)
                        continue

                    frame = _protocol.parse_response(msg.data)
                    if frame.message_type == _protocol.MSG_ERROR:
                        err_msg = ""
                        if frame.payload_json:
                            err_msg = str(frame.payload_json.get("error", ""))
                        if not err_msg:
                            err_msg = frame.payload.decode("utf-8", errors="ignore")
                        raise map_asr_error(
                            frame.error_code or -1,
                            err_msg,
                            request_id=request_id,
                        )

                    if frame.payload_json is None:
                        continue

                    self._process_payload(frame, request_id=request_id)

                    if frame.is_last:
                        return
            except APIError as e:
                recv_error.append(e)
                raise

        send = asyncio.create_task(send_task())
        recv = asyncio.create_task(recv_task())

        try:
            await asyncio.gather(send, recv)
        finally:
            await ws.close()
            await utils.aio.gracefully_cancel(send, recv)
            if recv_error and self._speaking:
                self._event_ch.send_nowait(
                    _lk_stt.SpeechEvent(type=_lk_stt.SpeechEventType.END_OF_SPEECH)
                )
                self._speaking = False

    _volc_stt.SpeechStream._run = _lampgo_volc_speech_stream_run
    print(
        "[lampgo] patched Volcengine ASR silence keepalive %.1fs (pid=%d)" %
        (_LAMPGO_ASR_KEEPALIVE_S, os.getpid()),
        file=sys.stderr,
        flush=True,
    )
except Exception as e:
    print("[lampgo] Volcengine ASR keepalive patch failed: %r" % (e,), file=sys.stderr, flush=True)

try:
    from livekit.agents.types import APIConnectOptions
    from livekit.agents import APIStatusError
    from livekit.plugins.volcengine import tts as _volc_tts

    _LAMPGO_VOLC_TTS_TIMEOUT = float(os.environ.get("LAMPGO_VOLCENGINE_TTS_TIMEOUT_S", "20"))
    _orig_volc_synthesize_via_ws = _volc_tts._synthesize_via_ws
    _orig_volc_synthesize_via_http = _volc_tts._synthesize_via_http

    def _lampgo_tts_conn_options(conn_options):
        if _LAMPGO_VOLC_TTS_TIMEOUT <= 0 or conn_options.timeout >= _LAMPGO_VOLC_TTS_TIMEOUT:
            return conn_options
        return APIConnectOptions(
            max_retry=conn_options.max_retry,
            retry_interval=conn_options.retry_interval,
            timeout=_LAMPGO_VOLC_TTS_TIMEOUT,
        )

    def _lampgo_tts_request_info(kwargs):
        text = kwargs.get("text") or ""
        request_id = kwargs.get("request_id") or "?"
        return request_id, len(str(text))

    def _lampgo_tts_uses_bidirectional_api(opts):
        voice = str(getattr(opts, "voice", "") or "")
        return (
            voice.startswith("S_")
            or voice.startswith("zh_")
            or voice.startswith("saturn_")
            or "_bigtts" in voice
        )

    async def _lampgo_synthesize_via_bidirectional_tts(*, opts, output_emitter, text, request_id):
        from lampgo.voice.tts import VolcengineTTS

        voice = str(getattr(opts, "voice", "") or "")
        print(
            "[lampgo] Volcengine TTS bidirectional start request_id=%s voice=%s text_len=%s (pid=%d)" %
            (request_id, voice, len(str(text or "")), os.getpid()),
            file=sys.stderr,
            flush=True,
        )
        tts_client = VolcengineTTS(
            app_id=getattr(opts, "app_id", ""),
            access_token=getattr(opts, "access_token", ""),
            voice=voice,
            sample_rate=int(getattr(opts, "sample_rate", 24000) or 24000),
        )
        audio_bytes = 0
        async for chunk in tts_client.stream_pcm(str(text or "")):
            if chunk:
                audio_bytes += len(chunk)
                output_emitter.push(chunk)
        if audio_bytes <= 0:
            raise APIStatusError(
                message="volcengine bidirectional TTS returned empty audio",
                status_code=-1,
                request_id=request_id,
                body=None,
                retryable=True,
            )
        print(
            "[lampgo] Volcengine TTS bidirectional done request_id=%s bytes=%s (pid=%d)" %
            (request_id, audio_bytes, os.getpid()),
            file=sys.stderr,
            flush=True,
        )

    async def _lampgo_synthesize_via_ws(*, conn_options, **kwargs):
        request_id, text_len = _lampgo_tts_request_info(kwargs)
        opts = kwargs.get("opts")
        if opts is not None and _lampgo_tts_uses_bidirectional_api(opts):
            try:
                return await _lampgo_synthesize_via_bidirectional_tts(
                    opts=opts,
                    output_emitter=kwargs["output_emitter"],
                    text=kwargs.get("text") or "",
                    request_id=request_id,
                )
            except Exception as e:
                print(
                    "[lampgo] Volcengine TTS bidirectional failed request_id=%s error=%r (pid=%d)" %
                    (request_id, e, os.getpid()),
                    file=sys.stderr,
                    flush=True,
                )
                raise
        print(
            "[lampgo] Volcengine TTS ws start request_id=%s text_len=%s (pid=%d)" %
            (request_id, text_len, os.getpid()),
            file=sys.stderr,
            flush=True,
        )
        try:
            result = await _orig_volc_synthesize_via_ws(
                conn_options=_lampgo_tts_conn_options(conn_options),
                **kwargs,
            )
        except Exception as e:
            print(
                "[lampgo] Volcengine TTS ws failed request_id=%s error=%r (pid=%d)" %
                (request_id, e, os.getpid()),
                file=sys.stderr,
                flush=True,
            )
            raise
        print(
            "[lampgo] Volcengine TTS ws done request_id=%s (pid=%d)" %
            (request_id, os.getpid()),
            file=sys.stderr,
            flush=True,
        )
        return result

    async def _lampgo_synthesize_via_http(*, conn_options, **kwargs):
        request_id, text_len = _lampgo_tts_request_info(kwargs)
        print(
            "[lampgo] Volcengine TTS http start request_id=%s text_len=%s (pid=%d)" %
            (request_id, text_len, os.getpid()),
            file=sys.stderr,
            flush=True,
        )
        try:
            result = await _orig_volc_synthesize_via_http(
                conn_options=_lampgo_tts_conn_options(conn_options),
                **kwargs,
            )
        except Exception as e:
            print(
                "[lampgo] Volcengine TTS http failed request_id=%s error=%r (pid=%d)" %
                (request_id, e, os.getpid()),
                file=sys.stderr,
                flush=True,
            )
            raise
        print(
            "[lampgo] Volcengine TTS http done request_id=%s (pid=%d)" %
            (request_id, os.getpid()),
            file=sys.stderr,
            flush=True,
        )
        return result

    _volc_tts._synthesize_via_ws = _lampgo_synthesize_via_ws
    _volc_tts._synthesize_via_http = _lampgo_synthesize_via_http
    print(
        "[lampgo] patched Volcengine TTS timeout to %.1fs with bidirectional voice routing (pid=%d)" %
        (_LAMPGO_VOLC_TTS_TIMEOUT, os.getpid()),
        file=sys.stderr,
        flush=True,
    )
except Exception as e:
    print("[lampgo] Volcengine TTS timeout patch failed: %r" % (e,), file=sys.stderr, flush=True)
"""


class AgentSDKManager:
    """Manage the lifecycle of the Lampgo LiveKit Agent SDK subprocess."""

    def __init__(self, voice_config: VoiceConfig, web_port: int = 8420) -> None:
        self._voice = voice_config
        self._web_port = web_port
        self._port = AGENT_SDK_PORT
        self._process: asyncio.subprocess.Process | None = None
        self._process_pgid: int | None = None
        self._roles_path: Path | None = None
        self._patch_dir: Path | None = None
        self._monitor_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()
        self._startup_failed_event = asyncio.Event()
        self._start_lock = asyncio.Lock()
        self._last_error = ""

    @property
    def port(self) -> int:
        return self._port

    @property
    def last_error(self) -> str:
        return self._last_error

    def _set_last_error(self, error: str) -> None:
        self._last_error = error

    def _local_livekit_server_reachable(self) -> bool:
        parsed = urlparse(self._voice.livekit_url or "")
        host = parsed.hostname or ""
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return True
        port = parsed.port or (443 if parsed.scheme in {"wss", "https"} else 80)
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            self._set_last_error(
                f"LiveKit server is not reachable at {self._voice.livekit_url}; start it before voice calls."
            )
            logger.warning(
                "agent_sdk.livekit_server_unreachable",
                livekit_url=self._voice.livekit_url,
                hint="start LiveKit server before starting a voice call",
            )
            return False

    def _can_start(self) -> bool:
        """Check that all required config fields are present."""
        self._set_last_error("")
        v = self._voice
        missing = []
        if not v.livekit_url:
            missing.append("livekit_url")
        if not v.volcengine_app_id:
            missing.append("volcengine_app_id")
        if not v.volcengine_access_token:
            missing.append("volcengine_access_token")
        if missing:
            self._set_last_error("Missing voice config: " + ", ".join(missing))
            logger.info("agent_sdk.missing_config", fields=missing)
            return False
        if not self._local_livekit_server_reachable():
            return False
        if importlib.util.find_spec(AGENT_SDK_MODULE) is None:
            self._set_last_error(f"{AGENT_SDK_PACKAGE} package is not installed")
            logger.warning(
                "agent_sdk.package_not_found",
                package=AGENT_SDK_PACKAGE,
                module=AGENT_SDK_MODULE,
                hint="Install voice extras: uv pip install lampgo[voice]",
            )
            return False
        return True

    def _generate_roles_yaml(self) -> Path:
        """Write a temporary roles.yaml from current config values."""
        livekit_http_url = _livekit_http_url(self._voice.livekit_url)
        tts_voice = self._voice.tts_voice or "zh_female_vv_uranus_bigtts"
        content = ROLES_YAML_TEMPLATE.format(
            livekit_url=_yaml_string(_livekit_ws_url(self._voice.livekit_url)),
            rtc_token_endpoint=_yaml_string(f"{livekit_http_url.rstrip('/')}/rtc/token"),
            agent_token_endpoint=_yaml_string(f"{livekit_http_url.rstrip('/')}/agent/token"),
            rtc_token_api_key=_yaml_string(
                os.environ.get("LAMPGO_RTC_TOKEN_API_KEY")
                or DEFAULT_LAMPGO_RTC_TOKEN_API_KEY
            ),
            agent_registration_token=_yaml_string(
                os.environ.get("LAMPGO_AGENT_REGISTRATION_TOKEN")
                or DEFAULT_LAMPGO_AGENT_REGISTRATION_TOKEN
            ),
            web_port=self._web_port,
            volcengine_app_id=_yaml_string(self._voice.volcengine_app_id),
            volcengine_access_token=_yaml_string(self._voice.volcengine_access_token),
            tts_voice=_yaml_string(tts_voice),
        )
        tmp = Path(tempfile.mktemp(suffix=".yaml", prefix="lampgo-roles-"))
        tmp.write_text(content, encoding="utf-8")
        logger.info("agent_sdk.roles_yaml_generated", path=str(tmp), tts_voice=tts_voice)
        return tmp

    def _generate_patch_dir(self) -> Path:
        """Create a directory containing ``sitecustomize.py`` for PYTHONPATH injection."""
        tmp = Path(tempfile.mkdtemp(prefix="lampgo-sdk-patch-"))
        (tmp / "sitecustomize.py").write_text(_SITECUSTOMIZE_CODE, encoding="utf-8")
        logger.info("agent_sdk.patch_dir_generated", path=str(tmp))
        return tmp

    def _resolve_sdk_binary(self) -> str | None:
        """Return path to the Agent SDK CLI binary, or None.

        Prefer the binary inside the active ``.venv`` so the right Python
        interpreter (with all our voice extras) is used.
        """
        bin_dir = Path(sys.executable).parent
        for binary in AGENT_SDK_BINARIES:
            venv_bin = bin_dir / binary
            if venv_bin.exists():
                return str(venv_bin)
        for binary in AGENT_SDK_BINARIES:
            which = shutil.which(binary)
            if which:
                return which
        return None

    @staticmethod
    def _psutil():
        """Import psutil lazily so missing voice extras still get a useful error."""
        try:
            import psutil
        except ImportError as exc:  # pragma: no cover - guarded by the voice extra
            raise RuntimeError("psutil is required to inspect the LiveKit Agent SDK port") from exc
        return psutil

    def _find_port_listener_pid(self) -> int | None:
        """Return the unique PID listening on the SDK port, if any."""
        psutil = self._psutil()
        listener_pids: set[int] = set()
        unknown_listener = False
        try:
            connections = [
                (connection, connection.pid)
                for connection in psutil.net_connections(kind="tcp")
            ]
        except (psutil.AccessDenied, OSError):
            # macOS can reject the whole system-wide query because of one
            # protected process. Fall back to inspecting current-user
            # processes individually so a single denial is harmless.
            connections = []
            for process in psutil.process_iter():
                if not self._process_is_current_user(process):
                    continue
                try:
                    connections.extend(
                        (connection, process.pid)
                        for connection in process.net_connections(kind="tcp")
                    )
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    continue

        for connection, owner_pid in connections:
            if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                continue
            local_port = getattr(connection.laddr, "port", None)
            if local_port is None and len(connection.laddr) >= 2:
                local_port = connection.laddr[1]
            if local_port != self._port:
                continue
            if owner_pid is None:
                unknown_listener = True
            else:
                listener_pids.add(int(owner_pid))

        if unknown_listener or len(listener_pids) > 1:
            raise RuntimeError(f"cannot determine the unique owner of TCP port {self._port}")
        if listener_pids:
            return next(iter(listener_pids))

        # If no visible process owns the port, distinguish a genuinely free
        # port from a listener hidden by OS permissions. Never assume the
        # latter is safe to terminate or replace.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", self._port))
            except OSError as exc:
                raise RuntimeError(
                    f"TCP port {self._port} is occupied but its owner cannot be inspected"
                ) from exc
        return None

    @staticmethod
    def _process_is_current_user(process) -> bool:
        try:
            if hasattr(os, "getuid"):
                return int(process.uids().real) == os.getuid()
            return process.username().casefold() == getpass.getuser().casefold()
        except Exception:
            return False

    def _identify_sdk_port_owner(self, listener_pid: int) -> _SDKPortOwner | None:
        """Resolve a listener to an SDK parent/process-group leader owned by this user."""
        psutil = self._psutil()
        try:
            listener = psutil.Process(listener_pid)
            if not self._process_is_current_user(listener):
                return None

            process_group_id: int | None = None
            candidates = []
            if os.name != "nt":
                try:
                    process_group_id = os.getpgid(listener_pid)
                    candidates.append(psutil.Process(process_group_id))
                except (OSError, psutil.Error):
                    process_group_id = None

            current = listener
            seen: set[int] = set()
            while current.pid not in seen and len(seen) < 8:
                seen.add(current.pid)
                candidates.append(current)
                try:
                    current = current.parent()
                except psutil.Error:
                    break
                if current is None:
                    break

            for candidate in candidates:
                if not self._process_is_current_user(candidate):
                    continue
                try:
                    command = candidate.cmdline()
                except psutil.Error:
                    continue
                if not _command_runs_agent_sdk(command):
                    continue
                if process_group_id is not None:
                    try:
                        if os.getpgid(candidate.pid) != process_group_id:
                            continue
                    except OSError:
                        continue
                return _SDKPortOwner(
                    listener_pid=listener_pid,
                    root_pid=int(candidate.pid),
                    process_group_id=process_group_id,
                    process_name=candidate.name(),
                )
        except psutil.Error:
            return None
        return None

    def _describe_process(self, pid: int) -> str:
        psutil = self._psutil()
        try:
            process = psutil.Process(pid)
            return process.name()
        except psutil.Error:
            return "unknown"

    def _signal_sdk_owner(self, owner: _SDKPortOwner, *, force: bool) -> None:
        """Signal only the verified SDK process group/tree."""
        psutil = self._psutil()
        if os.name != "nt" and owner.process_group_id is not None:
            if owner.process_group_id == os.getpgrp():
                raise RuntimeError("refusing to signal LampGo's own process group")
            signal_to_send = signal.SIGKILL if force else signal.SIGTERM
            try:
                os.killpg(owner.process_group_id, signal_to_send)
            except ProcessLookupError:
                pass
            return

        try:
            root = psutil.Process(owner.root_pid)
            process_tree = root.children(recursive=True) + [root]
        except psutil.NoSuchProcess:
            return
        for process in reversed(process_tree):
            try:
                process.kill() if force else process.terminate()
            except psutil.NoSuchProcess:
                pass

    async def _wait_for_port_free(self, timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            try:
                listener_pid = await asyncio.to_thread(self._find_port_listener_pid)
            except RuntimeError:
                return False
            if listener_pid is None:
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.1)

    async def _release_sdk_port(self) -> bool:
        """Replace a stale LampGo SDK listener; never kill an unknown process."""
        try:
            listener_pid = await asyncio.to_thread(self._find_port_listener_pid)
        except RuntimeError as exc:
            self._set_last_error(str(exc))
            logger.warning("agent_sdk.port_inspection_failed", port=self._port, error=str(exc))
            return False
        if listener_pid is None:
            return True

        owner = await asyncio.to_thread(self._identify_sdk_port_owner, listener_pid)
        if owner is None:
            process_name = await asyncio.to_thread(self._describe_process, listener_pid)
            self._set_last_error(
                f"TCP port {self._port} is occupied by an unknown process "
                f"(pid={listener_pid}, process={process_name}); refusing to terminate it"
            )
            logger.warning(
                "agent_sdk.port_owned_by_unknown_process",
                port=self._port,
                pid=listener_pid,
                process=process_name,
            )
            return False

        logger.warning(
            "agent_sdk.stale_process_detected",
            port=self._port,
            listener_pid=owner.listener_pid,
            root_pid=owner.root_pid,
            pgid=owner.process_group_id,
            process=owner.process_name,
        )
        try:
            await asyncio.to_thread(self._signal_sdk_owner, owner, force=False)
            if await self._wait_for_port_free(3.0):
                logger.info("agent_sdk.stale_process_stopped", port=self._port, root_pid=owner.root_pid)
                return True

            logger.warning(
                "agent_sdk.stale_process_stop_timeout",
                port=self._port,
                root_pid=owner.root_pid,
                pgid=owner.process_group_id,
            )
            await asyncio.to_thread(self._signal_sdk_owner, owner, force=True)
            if await self._wait_for_port_free(2.0):
                logger.info("agent_sdk.stale_process_killed", port=self._port, root_pid=owner.root_pid)
                return True
        except Exception as exc:
            self._set_last_error(f"failed to stop stale {AGENT_SDK_PACKAGE}: {exc}")
            logger.exception("agent_sdk.stale_process_cleanup_failed", root_pid=owner.root_pid)
            return False

        self._set_last_error(
            f"stale {AGENT_SDK_PACKAGE} did not release TCP port {self._port}"
        )
        return False

    @staticmethod
    def _merge_no_proxy(value: str | None) -> str:
        existing = [item.strip() for item in (value or "").split(",") if item.strip()]
        seen = {item.lower() for item in existing}
        for host in _LOCAL_NO_PROXY_HOSTS:
            if host.lower() not in seen:
                existing.append(host)
                seen.add(host.lower())
        return ",".join(existing)

    async def start(self) -> bool:
        """Start the Agent SDK subprocess if config is complete."""
        async with self._start_lock:
            return await self._start_locked()

    async def _start_locked(self) -> bool:
        if self.is_running:
            logger.debug("agent_sdk.already_running")
            return True
        if self._process is not None:
            # A previous parent may already have exited while worker children
            # still hold stdout or sockets. Reap its whole managed group first.
            await self.stop()

        if not self._can_start():
            return False

        sdk_binary = self._resolve_sdk_binary()
        if sdk_binary is None:
            binaries = ", ".join(AGENT_SDK_BINARIES)
            self._set_last_error(f"Agent SDK CLI is missing from PATH and venv/bin: {binaries}")
            logger.warning(
                "agent_sdk.binary_not_found",
                binaries=AGENT_SDK_BINARIES,
                hint=f"Agent SDK CLI is missing from PATH and venv/bin: {binaries}",
            )
            return False

        if not await self._release_sdk_port():
            return False

        self._roles_path = self._generate_roles_yaml()
        self._patch_dir = self._generate_patch_dir()

        # Prepend our patch dir to PYTHONPATH so sitecustomize.py is auto-loaded
        # by the SDK's main process AND every multiprocessing-spawned worker.
        env = os.environ.copy()
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{self._patch_dir}{os.pathsep}{existing_pp}" if existing_pp else str(self._patch_dir)
        )
        call_mode = str(getattr(self._voice, "call_mode", "") or "stable").strip().lower().replace("-", "_")
        call_mode = {
            "safe": "stable",
            "half_duplex": "stable",
            "interrupt": "interruptible",
            "interruptions": "interruptible",
            "barge_in": "interruptible",
            "aec": "esp32_aec",
            "experimental_aec": "esp32_aec",
        }.get(call_mode, call_mode)
        allow_interruptions = (
            call_mode in {"interruptible", "esp32_aec"}
            if call_mode
            else bool(self._voice.livekit_allow_interruptions)
        )
        env["LAMPGO_LIVEKIT_ALLOW_INTERRUPTIONS"] = "1" if allow_interruptions else "0"
        no_proxy = self._merge_no_proxy(
            ",".join(value for value in (env.get("NO_PROXY"), env.get("no_proxy")) if value)
        )
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy
        logger.info("agent_sdk.no_proxy_configured", no_proxy=no_proxy)

        self._ready_event.clear()
        self._startup_failed_event.clear()
        try:
            self._process = await asyncio.create_subprocess_exec(
                sdk_binary,
                "--config-file", str(self._roles_path),
                "--host", "127.0.0.1",
                "--port", str(self._port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            try:
                self._process_pgid = os.getpgid(self._process.pid)
            except OSError:
                self._process_pgid = None
            self._monitor_task = asyncio.create_task(self._monitor())
            logger.info(
                "agent_sdk.started",
                pid=self._process.pid,
                pgid=self._process_pgid,
                roles_yaml=str(self._roles_path),
                patch_dir=str(self._patch_dir),
            )
            return True
        except Exception:
            self._set_last_error(f"failed to start {AGENT_SDK_PACKAGE}")
            logger.exception("agent_sdk.start_failed")
            self._cleanup_roles()
            self._cleanup_patch_dir()
            return False

    async def stop(self) -> None:
        """Gracefully stop the Agent SDK subprocess."""
        if self._process is None:
            return

        proc = self._process
        pid = proc.pid
        pgid = self._process_pgid
        logger.info("agent_sdk.stopping", pid=pid, pgid=pgid)

        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning("agent_sdk.stop_timeout_killing", pid=pid, pgid=pgid)
                if pgid is not None:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("agent_sdk.stop_error", pid=pid, pgid=pgid)

        if self._process is proc:
            self._process = None
        self._process_pgid = None
        self._ready_event.clear()
        self._startup_failed_event.clear()

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None

        self._cleanup_roles()
        self._cleanup_patch_dir()
        logger.info("agent_sdk.stopped", pid=pid, pgid=pgid)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def is_ready(self) -> bool:
        return self.is_running and self._ready_event.is_set()

    async def wait_ready(self, timeout_s: float = 8.0) -> bool:
        if self.is_ready:
            return True
        if not self.is_running or self._startup_failed_event.is_set():
            return False

        waiters = {
            asyncio.create_task(self._ready_event.wait()),
            asyncio.create_task(self._startup_failed_event.wait()),
        }
        done: set[asyncio.Task] = set()
        try:
            done, _ = await asyncio.wait(
                waiters,
                timeout=timeout_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
        if not done:
            self._set_last_error(f"voice agent SDK did not become ready within {timeout_s:.1f}s")
            return False
        return not self._startup_failed_event.is_set() and self.is_ready

    async def _monitor(self) -> None:
        """Read stdout and watch for unexpected exits."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            async for line in proc.stdout:
                text = line.decode(errors="replace").rstrip()
                if text:
                    logger.info("agent_sdk.output", line=text)
                    lowered = text.casefold()
                    if any(marker in lowered for marker in _BIND_FAILURE_MARKERS):
                        self._set_last_error(f"TCP port {self._port} became unavailable during SDK startup")
                        self._startup_failed_event.set()
                    if "registered worker" in text:
                        self._ready_event.set()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("agent_sdk.monitor_read_error", exc_info=True)

        rc = await proc.wait()
        if rc is not None and rc != 0:
            if not self._last_error:
                self._set_last_error(f"{AGENT_SDK_PACKAGE} exited with status {rc}")
            self._startup_failed_event.set()
            logger.warning("agent_sdk.exited_unexpectedly", returncode=rc)
        elif not self._ready_event.is_set():
            self._set_last_error(f"{AGENT_SDK_PACKAGE} exited before becoming ready")
            self._startup_failed_event.set()
        if self._process is proc:
            self._process = None
            self._process_pgid = None
            self._ready_event.clear()
            self._cleanup_roles()
            self._cleanup_patch_dir()

    def _cleanup_patch_dir(self) -> None:
        if self._patch_dir and self._patch_dir.exists():
            try:
                shutil.rmtree(self._patch_dir)
            except OSError:
                pass
            self._patch_dir = None

    def _cleanup_roles(self) -> None:
        if self._roles_path and self._roles_path.exists():
            try:
                self._roles_path.unlink()
            except OSError:
                pass
            self._roles_path = None
