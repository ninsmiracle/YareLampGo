"""Xiaomi LiveKit Agent SDK subprocess manager.

Generates ``roles.yaml`` from lampgo's VoiceConfig at runtime and
manages the ``xiaomi-livekit-agent`` child process lifecycle.
The Agent SDK process connects to the LiveKit server, subscribes to
audio in the room, runs ASR/TTS via Volcengine, and calls lampgo's
``/v1/chat/completions`` endpoint for LLM responses.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from lampgo.core.config import VoiceConfig

logger = structlog.get_logger(__name__)

ROLES_YAML_TEMPLATE = """\
version: 1

livekit:
  url: {livekit_url}
  api_key: {livekit_api_key}
  api_secret: {livekit_api_secret}

token_api:
  shared_secret: ""

agent:
  name_prefix: xiaomi-agent

providers:
  lampgo_llm:
    type: openai
    api_key: lampgo-local
    base_url: http://127.0.0.1:{web_port}/v1

  volc_main:
    type: volcengine
    app_id: "{volcengine_app_id}"
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
      voice: {livekit_tts_voice}
      sample_rate: 24000
      streaming: true

voice_agents:
  - name: lampgo-jarvis
    display_name: "desk lamp voice assistant"
    llm:
      system_prompt: "You are the desk lamp's voice interface. Preserve the backend persona identity and reply in the same language as the user. Keep answers short, no Markdown or emoji."
"""


AGENT_SDK_PORT = 18790

# ``sitecustomize.py`` is auto-imported by every Python interpreter that has
# the containing directory on ``sys.path``.  By staging this file in a
# dedicated dir and prepending it to ``PYTHONPATH`` before launching the SDK
# binary, we guarantee the patch is applied to:
#   1. The SDK's main process (token API).
#   2. Every multiprocessing-spawned worker process (LiveKit agent worker).
#
# This is essential because xiaomi-livekit-agent uses ``multiprocessing.spawn``
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
"""


class AgentSDKManager:
    """Manage the lifecycle of the Xiaomi LiveKit Agent SDK subprocess."""

    def __init__(self, voice_config: VoiceConfig, web_port: int = 8420) -> None:
        self._voice = voice_config
        self._web_port = web_port
        self._port = AGENT_SDK_PORT
        self._process: asyncio.subprocess.Process | None = None
        self._roles_path: Path | None = None
        self._patch_dir: Path | None = None
        self._monitor_task: asyncio.Task | None = None

    @property
    def port(self) -> int:
        return self._port

    def _can_start(self) -> bool:
        """Check that all required config fields are present."""
        v = self._voice
        missing = []
        if not v.livekit_url:
            missing.append("livekit_url")
        if not v.livekit_api_key:
            missing.append("livekit_api_key")
        if not v.livekit_api_secret:
            missing.append("livekit_api_secret")
        if not v.volcengine_app_id:
            missing.append("volcengine_app_id")
        if not v.volcengine_access_token:
            missing.append("volcengine_access_token")
        if missing:
            logger.info("agent_sdk.missing_config", fields=missing)
            return False
        try:
            import xiaomi_livekit_agent  # noqa: F401
        except ImportError:
            logger.warning(
                "agent_sdk.package_not_found",
                hint="Install voice extras: uv pip install lampgo[voice]",
            )
            return False
        return True

    def _generate_roles_yaml(self) -> Path:
        """Write a temporary roles.yaml from current config values."""
        content = ROLES_YAML_TEMPLATE.format(
            livekit_url=self._voice.livekit_url,
            livekit_api_key=self._voice.livekit_api_key,
            livekit_api_secret=self._voice.livekit_api_secret,
            web_port=self._web_port,
            volcengine_app_id=self._voice.volcengine_app_id,
            volcengine_access_token=self._voice.volcengine_access_token,
            livekit_tts_voice=self._voice.livekit_tts_voice or "BV700_streaming",
        )
        tmp = Path(tempfile.mktemp(suffix=".yaml", prefix="lampgo-roles-"))
        tmp.write_text(content, encoding="utf-8")
        logger.info("agent_sdk.roles_yaml_generated", path=str(tmp))
        return tmp

    def _generate_patch_dir(self) -> Path:
        """Create a directory containing ``sitecustomize.py`` for PYTHONPATH injection."""
        tmp = Path(tempfile.mkdtemp(prefix="lampgo-sdk-patch-"))
        (tmp / "sitecustomize.py").write_text(_SITECUSTOMIZE_CODE, encoding="utf-8")
        logger.info("agent_sdk.patch_dir_generated", path=str(tmp))
        return tmp

    def _resolve_sdk_binary(self) -> str | None:
        """Return path to the ``xiaomi-livekit-agent`` CLI binary, or None.

        Prefer the binary inside the active ``.venv`` so the right Python
        interpreter (with all our voice extras) is used.
        """
        venv_bin = Path(sys.executable).parent / "xiaomi-livekit-agent"
        if venv_bin.exists():
            return str(venv_bin)
        which = shutil.which("xiaomi-livekit-agent")
        return which

    async def start(self) -> bool:
        """Start the Agent SDK subprocess if config is complete."""
        if self._process is not None:
            logger.debug("agent_sdk.already_running")
            return True

        if not self._can_start():
            return False

        sdk_binary = self._resolve_sdk_binary()
        if sdk_binary is None:
            logger.warning(
                "agent_sdk.binary_not_found",
                hint="xiaomi-livekit-agent CLI is missing from PATH and venv/bin",
            )
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

        try:
            self._process = await asyncio.create_subprocess_exec(
                sdk_binary,
                "--config-file", str(self._roles_path),
                "--host", "127.0.0.1",
                "--port", str(self._port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            self._monitor_task = asyncio.create_task(self._monitor())
            logger.info(
                "agent_sdk.started",
                pid=self._process.pid,
                roles_yaml=str(self._roles_path),
                patch_dir=str(self._patch_dir),
            )
            return True
        except Exception:
            logger.exception("agent_sdk.start_failed")
            self._cleanup_roles()
            self._cleanup_patch_dir()
            return False

    async def stop(self) -> None:
        """Gracefully stop the Agent SDK subprocess."""
        if self._process is None:
            return

        pid = self._process.pid
        logger.info("agent_sdk.stopping", pid=pid)

        try:
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("agent_sdk.stop_error", pid=pid)

        self._process = None

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None

        self._cleanup_roles()
        self._cleanup_patch_dir()
        logger.info("agent_sdk.stopped", pid=pid)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

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
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("agent_sdk.monitor_read_error", exc_info=True)

        rc = proc.returncode
        if rc is not None and rc != 0:
            logger.warning("agent_sdk.exited_unexpectedly", returncode=rc)
        self._process = None
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
