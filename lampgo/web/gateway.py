"""Web gateway — Starlette app serving REST API, WebSocket, and static UI."""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import json
import re
import time
import uuid
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx as httpx_module
import structlog
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from lampgo.core.config import LLMConfig, WebConfig
from lampgo.core.events import AgentFinished, ChatMessage, IntentResolved, IntentRouting
from lampgo.core.led import led_expression_catalog
from lampgo.device.audio_stream import redact_ws_owner_token
from lampgo.expression_clips import (
    ExpressionClipError,
    create_expression_clip,
    list_expression_clips,
    load_expression_clip,
    load_expression_clip_lcd_payload,
    update_expression_clip_sync,
)
from lampgo.expression_library import (
    ExpressionLibraryError,
    expression_capabilities,
    expression_schemas,
    eye_source_path,
    eye_storage_id,
    list_expression_presets,
    list_eyes,
    list_led_effects,
    resolve_expression,
    save_expression_preset,
    save_led_effect,
    set_eye_default_led,
)
from lampgo.perception.camera import CameraCapture
from lampgo.recordings import (
    RECORDING_NAME_ERROR,
    build_recording_actions_prompt,
    list_recording_catalog,
    normalize_recording_name,
    recording_description_path,
    write_recording_description,
)
from lampgo.web.ws_bridge import WsBridge

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ASSETS_DIR = REPO_ROOT / "assets"

_AUTH_COOKIE_NAME = "lampgo_auth"
_AUTH_COOKIE_MAX_AGE = 12 * 60 * 60
_PROTECTED_HTTP_PREFIXES = ("/api/", "/v1/")
_SAFE_CORS_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
_SAFE_CORS_HEADERS = ["Authorization", "Content-Type", "X-Lampgo-Token", "X-Requested-With"]
_LOCAL_LLM_COMPAT_TOKEN = "lampgo-local"
_SENSITIVE_CONFIG_FIELDS = frozenset(
    {
        "voice.livekit_api_key",
        "voice.livekit_api_secret",
        "voice.volcengine_access_token",
    }
)
_SAFETY_MAX_VELOCITY_LIMIT = 240.0
_SAFETY_MAX_ACCELERATION_LIMIT = 1800.0
_ESP32_PROBE_PATHS = frozenset({"/status", "/scan", "/connect", "/config"})
_LLM_LOCAL_PROVIDER_ALLOWLIST = frozenset({"ollama"})


class LampgoStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]) -> Any:
        response = await super().get_response(path, scope)
        if path in {"", ".", "index.html"} or path.endswith((".html", ".css", ".js")):
            response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        return response


def _coerce_value(current: Any, value: Any) -> Any:
    """Best-effort coerce ``value`` to the same Python type as ``current``.

    Used when patching LampgoConfig fields from JSON payloads that might
    arrive as strings (e.g. numeric inputs from HTML forms). We only coerce
    when the target field already has a typed value; otherwise the raw value
    is returned and pydantic will do its own validation at load time.
    """
    if value is None or current is None:
        return value
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(float(value))
        return current
    if isinstance(current, float):
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            return float(value)
        return current
    if isinstance(current, str):
        return "" if value is None else str(value)
    return value


# Max bytes of history we accept from the browser per request. The UI caps
# history_turns at 200 (= 400 messages), but we still want a hard wall so a
# malicious or buggy client can't push MB-scale blobs through the socket.
_MAX_HISTORY_BYTES = 256 * 1024
_MAX_HISTORY_ITEMS = 400


def _sanitize_chat_history(raw: Any) -> list[dict[str, str]]:
    """Normalize a chat-history payload from the frontend into a safe list of
    ``{role, content}`` dicts for the LLM prompt.

    The browser sends this alongside each /api/text and /api/audio call so the
    agent loop can see the last N turns of the current session (see
    LLMConfig.history_turns). We can't trust it blindly:

    * Only ``user`` and ``assistant`` roles are forwarded — system prompts
      stay server-owned.
    * Empty or non-string content is dropped.
    * The whole payload is capped in item count and total bytes so a runaway
      client can't bloat prompt tokens or spike request latency.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    total_bytes = 0
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        if role not in ("user", "assistant"):
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        total_bytes += len(text.encode("utf-8", errors="replace"))
        if total_bytes > _MAX_HISTORY_BYTES:
            break
        out.append({"role": role, "content": text})
        if len(out) >= _MAX_HISTORY_ITEMS:
            break
    return out


class WebGateway:
    """HTTP + WebSocket gateway that wraps a LampgoServer."""

    def __init__(self, server: LampgoServer, config: WebConfig | None = None) -> None:
        self.server = server
        self.config = config or WebConfig()
        self.bridge = WsBridge(server.events)
        self._status_task: asyncio.Task | None = None
        self._pet_pose_task: asyncio.Task | None = None
        self._active_request_tasks: dict[WebSocket, asyncio.Task] = {}
        self._background_ws_tasks: set[asyncio.Task[None]] = set()
        self._esp32_relay_tasks: dict[WebSocket, asyncio.Task] = {}
        self._esp32_speaker_clients: set[WebSocket] = set()
        self._esp32_capture_active = False
        self._livekit_token_lock = asyncio.Lock()
        self._livekit_room_lock = asyncio.Lock()
        self._livekit_token_gate_until = 0.0
        self._livekit_token_gate_owner = ""
        self._livekit_active_rooms: dict[str, dict[str, Any]] = {}
        self._rate_limit_buckets: dict[str, list[float]] = {}
        self.app = self._build_app()

    def _build_app(self) -> Starlette:
        routes = [
            Route("/api/text", self.api_text, methods=["POST"]),
            Route("/api/invoke", self.api_invoke, methods=["POST"]),
            Route("/api/status", self.api_status),
            Route("/api/skills", self.api_skills),
            Route("/api/skills/save", self.api_skills_save, methods=["POST"]),
            Route("/api/skills/delete", self.api_skills_delete, methods=["POST"]),
            Route("/api/skills/reload", self.api_skills_reload, methods=["POST"]),
            Route("/api/recordings", self.api_recordings),
            Route("/api/recordings/save", self.api_recordings_save, methods=["POST"]),
            Route("/api/recordings/update", self.api_recordings_update, methods=["POST"]),
            Route("/api/recordings/delete", self.api_recordings_delete, methods=["POST"]),
            Route("/api/recordings/aliases", self.api_recording_aliases, methods=["GET", "POST"]),
            Route("/api/expressions", self.api_expressions),
            Route("/api/expression-clips", self.api_expression_clips, methods=["GET", "POST"]),
            Route("/api/eyes", self.api_eyes, methods=["GET"]),
            Route("/api/eyes/{eye_id:str}", self.api_eye_update, methods=["POST"]),
            Route("/api/eyes/{eye_id:str}/source", self.api_eye_source, methods=["GET"]),
            Route("/api/eyes/{eye_id:str}/sync", self.api_eye_sync, methods=["POST"]),
            Route("/api/led-effects", self.api_led_effects, methods=["GET", "POST"]),
            Route("/api/expression-presets", self.api_expression_presets, methods=["GET", "POST"]),
            Route("/api/expressions/preview", self.api_expression_preview, methods=["POST"]),
            Route("/api/expressions/play", self.api_expression_play, methods=["POST"]),
            Route("/api/expressions/stop", self.api_expression_stop, methods=["POST"]),
            Route("/api/device/expression-capabilities", self.api_expression_capabilities, methods=["GET"]),
            Route("/api/camera/snap", self.api_camera_snap),
            Route("/api/sensor/context", self.api_sensor_context),
            Route("/api/agent/ask", self.api_agent_ask, methods=["POST"]),
            Route("/api/agent/ask/reply", self.api_agent_ask_reply, methods=["POST"]),
            Route("/api/agent/callback", self.api_agent_callback, methods=["POST"]),
            Route("/api/agent/tasks", self.api_agent_tasks),
            Route("/api/agent/tasks/{task_id:str}/cancel", self.api_agent_cancel, methods=["POST"]),
            Route("/api/agent/health", self.api_agent_health),
            Route("/api/livekit/token", self.api_livekit_token, methods=["POST"]),
            Route("/api/livekit/room/end", self.api_livekit_room_end, methods=["POST"]),
            Route("/api/cancel", self.api_cancel, methods=["POST"]),
            Route("/api/estop", self.api_estop, methods=["POST"]),
            # ---- user-editable config / persona / memory ----
            Route("/api/config", self.api_config_all, methods=["GET"]),
            Route("/api/config/device", self.api_config_device, methods=["POST"]),
            Route("/api/config/voice", self.api_config_voice, methods=["POST"]),
            Route("/api/config/motion", self.api_config_motion, methods=["POST"]),
            Route("/api/config/safety", self.api_config_safety, methods=["POST"]),
            Route("/api/config/web", self.api_config_web, methods=["POST"]),
            Route("/api/config/device_esp32", self.api_config_device_esp32, methods=["POST"]),
            Route("/api/config/detect", self.api_config_detect, methods=["POST"]),
            Route("/api/config/restart", self.api_config_restart, methods=["POST"]),
            Route("/api/config/llm", self.api_config_llm, methods=["GET", "POST"]),
            # ---- ESP32 wireless device (discovery, proxy, wifi setup) ----
            Route("/api/device/status", self.api_esp32_status, methods=["GET"]),
            Route("/api/device/snapshot", self.api_esp32_snapshot, methods=["GET"]),
            Route("/api/device/config", self.api_esp32_config, methods=["GET", "POST"]),
            Route("/api/device/led", self.api_esp32_led, methods=["GET", "POST"]),
            Route("/api/device/expression-clips/sync", self.api_esp32_expression_clip_sync, methods=["POST"]),
            Route("/api/device/pair", self.api_esp32_pair, methods=["POST"]),
            Route("/api/device/unpair", self.api_esp32_unpair, methods=["POST"]),
            Route("/api/device/claim", self.api_esp32_claim, methods=["POST"]),
            Route("/api/device/release", self.api_esp32_release, methods=["POST"]),
            Route("/api/device/reboot", self.api_esp32_reboot, methods=["POST"]),
            Route("/api/device/forget-wifi", self.api_esp32_forget_wifi, methods=["POST"]),
            Route("/api/device/probe", self.api_esp32_probe, methods=["POST"]),
            Route("/api/device/discovery/restart", self.api_esp32_restart_discovery, methods=["POST"]),
            Route("/api/device/capture-audio/start", self.api_esp32_capture_start, methods=["POST"]),
            Route("/api/device/capture-audio/stop", self.api_esp32_capture_stop, methods=["POST"]),
            Route("/api/device/capture-audio/cancel", self.api_esp32_capture_cancel, methods=["POST"]),
            WebSocketRoute("/api/device/speaker", self.ws_esp32_speaker),
            Route("/api/persona", self.api_persona_all, methods=["GET"]),
            Route("/api/persona/reset", self.api_persona_reset, methods=["POST"]),
            Route("/api/persona/{name:str}", self.api_persona_single, methods=["GET", "PUT"]),
            Route("/api/memory/core", self.api_memory_core, methods=["GET", "PUT"]),
            Route("/api/memory/core/reset", self.api_memory_core_reset, methods=["POST"]),
            Route("/api/memory/daily", self.api_memory_daily, methods=["GET", "POST"]),
            Route("/api/memory/summarize", self.api_memory_summarize, methods=["POST"]),
            Route("/api/debug/system-prompt", self.api_debug_system_prompt, methods=["GET"]),
            # ---- server-side persistent cache for chat sessions ----
            Route("/api/sessions", self.api_sessions, methods=["GET", "PUT", "DELETE"]),
            Route("/api/sessions/{session_id:str}", self.api_session_single, methods=["DELETE"]),
            # ---- event replay (so newly-connected browsers see historical events) ----
            Route("/api/events", self.api_events_replay, methods=["GET"]),
            WebSocketRoute("/ws", self.ws_endpoint),
            # ---- OpenAI-compatible LLM endpoint (for LiveKit Agent SDK) ----
            Route("/v1/chat/completions", self._llm_compat_handler, methods=["POST"]),
            # ---- pet model runtime visualization asset ----
            Route("/assets/pet/lampgo.glb", self.api_pet_model, methods=["GET"]),
            Route("/assets/pet/lampgoGLB.glb", self.api_pet_model, methods=["GET"]),
        ]
        if STATIC_DIR.is_dir():
            routes.append(Mount("/", app=LampgoStaticFiles(directory=str(STATIC_DIR), html=True)))

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
            app.state.lampgo_server = self.server
            self._status_task = asyncio.create_task(self._status_loop())
            self._pet_pose_task = asyncio.create_task(self._pet_pose_loop())
            yield
            for task in (self._status_task, self._pet_pose_task):
                if not task:
                    continue
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            background_tasks = list(self._background_ws_tasks)
            for task in background_tasks:
                task.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)
            self._background_ws_tasks.clear()

        app = Starlette(routes=routes, lifespan=lifespan)
        app.add_middleware(BaseHTTPMiddleware, dispatch=self._http_security_middleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self._allowed_cors_origins(),
            allow_credentials=True,
            allow_methods=_SAFE_CORS_METHODS,
            allow_headers=_SAFE_CORS_HEADERS,
        )
        return app

    async def _status_loop(self) -> None:
        """Periodically broadcast device status to all WS clients."""
        while True:
            await asyncio.sleep(self.config.status_interval)
            if self.bridge.client_count == 0:
                continue
            try:
                status = self.server._handle_status()
                await self.bridge.broadcast_status(status.get("result", {}))
            except Exception:
                logger.exception("web.status_loop_error")

    async def _pet_pose_loop(self) -> None:
        """Broadcast joint poses at animation-friendly cadence for the Web pet."""
        interval = 1.0 / 15.0
        while True:
            await asyncio.sleep(interval)
            if self.bridge.client_count == 0:
                continue
            try:
                await self.bridge.broadcast_pet_pose(self._pet_pose_snapshot())
            except Exception:
                logger.exception("web.pet_pose_loop_error")

    def _pet_pose_snapshot(self) -> dict[str, Any]:
        positions = self.server.motion.current_state.positions
        virtual = bool(getattr(self.server.motion, "is_virtual", False))
        return {
            "joint_positions": positions,
            "mode": "virtual" if virtual else "hardware",
            "no_hw": bool(self.server.config.no_hw),
            "hal_connected": bool(self.server.hal.is_connected),
            "running_skill": self.server.executor.current_skill_id,
            "is_busy": self.server.executor.is_busy,
        }

    def _allowed_cors_origins(self) -> list[str]:
        port = int(getattr(self.config, "port", 8420) or 8420)
        hosts = {"localhost", "127.0.0.1", "[::1]"}
        host = str(getattr(self.config, "host", "") or "").strip()
        if host and host not in {"0.0.0.0", "::"}:
            hosts.add(host)
        return [f"http://{host}:{port}" for host in sorted(hosts)]

    @staticmethod
    def _is_loopback_host(host: str | None) -> bool:
        if not host:
            return False
        clean = host.strip().strip("[]").lower()
        if clean in {"localhost", "testclient"}:
            return True
        try:
            return ipaddress.ip_address(clean).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _same_origin(origin: str, scheme: str, host: str | None, port: int | None) -> bool:
        try:
            parsed = urlsplit(origin)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        try:
            origin_port = parsed.port
        except ValueError:
            return False
        return parsed.scheme == scheme and parsed.hostname == host and origin_port == port

    def _origin_allowed(self, request: Request) -> bool:
        origin = request.headers.get("origin", "").strip()
        if not origin:
            return True
        if origin == "null":
            return False
        try:
            parsed = urlsplit(origin)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        if self._is_loopback_host(parsed.hostname):
            return True
        return self._same_origin(origin, request.url.scheme, request.url.hostname, request.url.port)

    def _expected_auth_token(self) -> str:
        from lampgo import personastore

        return personastore.get_or_create_local_api_token()

    @staticmethod
    def _bearer_token(value: str | None) -> str:
        if not value:
            return ""
        prefix = "bearer "
        stripped = value.strip()
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix):].strip()
        return ""

    def _request_token_candidates(self, request: Request) -> list[str]:
        return [
            self._bearer_token(request.headers.get("authorization")),
            str(request.headers.get("x-lampgo-token") or "").strip(),
            str(request.cookies.get(_AUTH_COOKIE_NAME) or "").strip(),
        ]

    def _is_test_client(self, request: Request | WebSocket) -> bool:
        client = getattr(request, "client", None)
        return bool(client and getattr(client, "host", "") == "testclient")

    def _is_loopback_client(self, request: Request | WebSocket) -> bool:
        client = getattr(request, "client", None)
        return bool(client and self._is_loopback_host(getattr(client, "host", "")))

    def _is_local_llm_compat_request(self, request: Request) -> bool:
        if request.url.path != "/v1/chat/completions":
            return False
        if not self._is_loopback_client(request):
            return False
        token = self._bearer_token(request.headers.get("authorization"))
        return bool(token and hmac.compare_digest(token, _LOCAL_LLM_COMPAT_TOKEN))

    def _is_request_authorized(self, request: Request) -> bool:
        if self._is_local_llm_compat_request(request):
            return True
        if self._is_test_client(request):
            return True
        try:
            expected = self._expected_auth_token()
        except Exception:
            logger.exception("web.auth_token_load_failed")
            return False
        if not expected:
            return False
        return any(token and hmac.compare_digest(token, expected) for token in self._request_token_candidates(request))

    def _rate_limit_ok(self, request: Request, *, limit: int = 600, window_s: float = 60.0) -> tuple[bool, int]:
        client = getattr(request, "client", None)
        key = getattr(client, "host", None) or "unknown"
        now = time.monotonic()
        bucket = [ts for ts in self._rate_limit_buckets.get(key, []) if now - ts < window_s]
        if len(bucket) >= limit:
            self._rate_limit_buckets[key] = bucket
            retry_after = max(1, int(window_s - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
        self._rate_limit_buckets[key] = bucket
        return True, 0

    def _security_response(
        self,
        payload: dict[str, Any],
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        response = JSONResponse(payload, status_code=status_code, headers=headers)
        self._apply_security_headers(response)
        return response

    async def _http_security_middleware(self, request: Request, call_next: Any):  # type: ignore[no-untyped-def]
        protected = request.url.path.startswith(_PROTECTED_HTTP_PREFIXES)
        if protected and not self._origin_allowed(request):
            logger.warning(
                "web.auth_origin_rejected",
                path=request.url.path,
                origin=request.headers.get("origin", ""),
                client=getattr(getattr(request, "client", None), "host", ""),
            )
            return self._security_response({"ok": False, "error": "origin not allowed"}, status_code=403)

        if protected and request.method != "OPTIONS":
            allowed, retry_after = self._rate_limit_ok(request)
            if not allowed:
                logger.warning(
                    "web.rate_limit_rejected",
                    path=request.url.path,
                    client=getattr(getattr(request, "client", None), "host", ""),
                )
                return self._security_response(
                    {"ok": False, "error": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            if not self._is_request_authorized(request):
                logger.warning(
                    "web.auth_rejected",
                    path=request.url.path,
                    client=getattr(getattr(request, "client", None), "host", ""),
                )
                return self._security_response({"ok": False, "error": "authentication required"}, status_code=401)

        response = await call_next(request)
        self._apply_security_headers(response)
        if self._is_loopback_client(request) and request.method in {"GET", "HEAD"}:
            try:
                response.set_cookie(
                    _AUTH_COOKIE_NAME,
                    self._expected_auth_token(),
                    max_age=_AUTH_COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="strict",
                    secure=request.url.scheme == "https",
                )
            except Exception:
                logger.exception("web.auth_cookie_issue_failed")
        return response

    @staticmethod
    def _apply_security_headers(response: Any) -> None:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self' http: https: ws: wss:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://esm.sh; "
            "style-src 'self' 'unsafe-inline'; "
            "worker-src 'self' blob:",
        )

    # ---- OpenAI-compatible LLM endpoint ----

    async def _llm_compat_handler(self, request: Request) -> StreamingResponse:
        from lampgo.web.llm_compat import handle_chat_completions

        return await handle_chat_completions(request)

    async def api_pet_model(self, request: Request) -> FileResponse | JSONResponse:
        """Serve the pet visualization GLB from repo-level assets if present."""
        asset_name = Path(request.url.path).name
        if asset_name not in {"lampgo.glb", "lampgoGLB.glb"}:
            return JSONResponse({"ok": False, "error": "pet model not allowed"}, status_code=404)
        path = REPO_ASSETS_DIR / asset_name
        if not path.exists():
            return JSONResponse({"ok": False, "error": "pet model not found"}, status_code=404)
        return FileResponse(path, media_type="model/gltf-binary")

    # ---- REST endpoints ----

    async def api_text(self, request: Request) -> JSONResponse:
        body = await request.json()
        text = body.get("input", "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty input"}, status_code=400)

        request_id = body.get("request_id", uuid.uuid4().hex[:12])
        await self.server.events.publish(IntentRouting(text=text, request_id=request_id))

        history = _sanitize_chat_history(body.get("history"))
        result = await self.server.handle_request({"cmd": "text", "input": text, "request_id": request_id, "history": history})

        intent_type = result.get("result", {}).get("type", "unknown")
        skill_id = result.get("result", {}).get("skill_id")
        chat_response = result.get("result", {}).get("response") or result.get("result", {}).get("chat_response")
        await self.server.events.publish(
            IntentResolved(
                intent_type=intent_type,
                skill_id=skill_id,
                chat_response=chat_response,
                source=result.get("result", {}).get("source", ""),
                detail=result.get("result", {}).get("detail"),
                matched_keyword=result.get("result", {}).get("matched_keyword"),
                request_id=request_id,
            )
        )
        if chat_response:
            await self.server.events.publish(ChatMessage(role="assistant", content=chat_response, request_id=request_id))

        result["request_id"] = request_id
        return JSONResponse(result)

    async def api_invoke(self, request: Request) -> JSONResponse:
        body = await request.json()
        result = await self.server.handle_request(
            {
                "cmd": "invoke",
                "skill_id": body.get("skill_id", ""),
                "params": body.get("params", {}),
                "wait": body.get("wait", True),
            }
        )
        if not result.get("ok") and not result.get("error"):
            nested_error = ((result.get("result") or {}).get("error") or "").strip()
            if nested_error:
                result["error"] = nested_error
        return JSONResponse(result)

    async def api_status(self, request: Request) -> JSONResponse:
        result = self.server._handle_status()
        return JSONResponse(result)

    async def api_skills(self, request: Request) -> JSONResponse:
        result = self.server._handle_skills()
        return JSONResponse(result)

    async def api_skills_save(self, request: Request) -> JSONResponse:
        """POST /api/skills/save — create or update a user composed skill.

        Body: ``{"definition": {...}, "overwrite": true}``

        Thin pass-through to ``Server._handle_skills_save`` — all the validation
        / persistence / live-registration happens there so Codex (through MCP)
        and the Web UI exercise the exact same path.
        """
        body = await request.json()
        result = self.server._handle_skills_save(body)
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    async def api_skills_delete(self, request: Request) -> JSONResponse:
        """POST /api/skills/delete — remove a user composed skill.

        Body: ``{"skill_id": "..."}``.  Factory skills are refused with HTTP 400.
        """
        body = await request.json()
        result = self.server._handle_skills_delete(body)
        if result.get("ok"):
            return JSONResponse(result)
        reason = (result.get("result") or {}).get("reason")
        status = 404 if reason == "not_found" else 400
        return JSONResponse(result, status_code=status)

    async def api_skills_reload(self, request: Request) -> JSONResponse:
        """POST /api/skills/reload — rescan ``~/.lampgo/skills/user/`` from disk.

        Useful if the user hand-edits a JSON file; no daemon restart needed.
        """
        result = self.server._handle_skills_reload()
        return JSONResponse(result)

    async def api_recordings(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "result": {"recordings": self._list_recordings()}})

    async def api_recordings_delete(self, request: Request) -> JSONResponse:
        """POST /api/recordings/delete — delete a user-created CSV recording.

        Only files in <recordings_dir>/user are removable from the Web UI.
        Built-in recordings in the repo assets directory are intentionally
        locked, because deleting them would mutate shipped content.
        """
        body = await request.json()
        name = normalize_recording_name(body.get("name", ""))
        if not name:
            return JSONResponse({"ok": False, "error": RECORDING_NAME_ERROR}, status_code=400)

        recordings_dir = Path(self.server.config.recordings_dir)
        user_dir = recordings_dir / "user"
        csv_path = user_dir / f"{name}.csv"
        if not csv_path.exists():
            if (recordings_dir / f"{name}.csv").exists():
                return JSONResponse({"ok": False, "error": "built-in recording cannot be deleted"}, status_code=400)
            return JSONResponse({"ok": False, "error": "user recording not found"}, status_code=404)

        csv_path.unlink()
        txt_path = recording_description_path(csv_path)
        if txt_path.exists():
            txt_path.unlink()

        removed_aliases: list[str] = []
        alias_path = recordings_dir / "aliases.json"
        if alias_path.exists():
            try:
                aliases = json.loads(alias_path.read_text(encoding="utf-8"))
            except Exception:
                aliases = {}
            if isinstance(aliases, dict):
                kept = {}
                for alias, target in aliases.items():
                    if str(target).strip() == name:
                        removed_aliases.append(str(alias))
                    else:
                        kept[str(alias)] = target
                if removed_aliases:
                    alias_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        self.server._refresh_llm_skill_tools()

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "status": "deleted",
                    "name": name,
                    "path": str(csv_path),
                    "removed_aliases": removed_aliases,
                    "recordings": self._list_recordings(),
                },
            }
        )

    async def api_recordings_update(self, request: Request) -> JSONResponse:
        """POST /api/recordings/update — edit metadata for a user-created recording."""
        body = await request.json()
        name = normalize_recording_name(body.get("name", ""))
        description = str(body.get("description", "") or body.get("prompt", "")).strip()
        expression = str(body.get("expression", "")).strip()
        expression_preset = str(body.get("expression_preset", "") or body.get("preset_id", "")).strip()
        if not name:
            return JSONResponse({"ok": False, "error": RECORDING_NAME_ERROR}, status_code=400)

        recordings_dir = Path(self.server.config.recordings_dir)
        user_dir = recordings_dir / "user"
        csv_path = user_dir / f"{name}.csv"
        if not csv_path.exists():
            if (recordings_dir / f"{name}.csv").exists():
                return JSONResponse({"ok": False, "error": "built-in recording cannot be edited"}, status_code=400)
            return JSONResponse({"ok": False, "error": "user recording not found"}, status_code=404)

        write_recording_description(csv_path, description, expression, expression_preset)
        self.server._refresh_llm_skill_tools()
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "status": "updated",
                    "name": name,
                    "path": str(csv_path),
                    "description": description or None,
                    "expression": expression or None,
                    "expression_preset": expression_preset or None,
                    "recordings": self._list_recordings(),
                },
            }
        )

    async def api_recordings_save(self, request: Request) -> JSONResponse:
        """POST /api/recordings/save — write a CSV recording + optional keyword alias.

        Body: { "name": "my_skill", "csv": "<csv content>", "alias": "触发词" (optional) }
        Saves to <recordings_dir>/user/<name>.csv (user-created recordings are isolated from
        built-in assets; the user/ subdirectory is gitignored).
        Updates aliases.json in recordings_dir root if alias provided.
        """
        body = await request.json()
        name = normalize_recording_name(body.get("name", ""))
        csv_content = body.get("csv", "")
        alias = str(body.get("alias", "")).strip()
        description = str(body.get("description", "") or body.get("prompt", "")).strip()
        expression = str(body.get("expression", "")).strip()
        expression_preset = str(body.get("expression_preset", "") or body.get("preset_id", "")).strip()

        if not name:
            return JSONResponse({"ok": False, "error": RECORDING_NAME_ERROR}, status_code=400)
        if not isinstance(csv_content, str) or not csv_content.strip():
            return JSONResponse({"ok": False, "error": "csv must be a non-empty string"}, status_code=400)

        recordings_dir = Path(self.server.config.recordings_dir)
        user_dir = recordings_dir / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        csv_path = user_dir / f"{name}.csv"
        csv_path.write_text(csv_content, encoding="utf-8")
        write_recording_description(csv_path, description, expression, expression_preset)

        if alias:
            alias_path = recordings_dir / "aliases.json"
            try:
                existing: dict = json.loads(alias_path.read_text(encoding="utf-8")) if alias_path.exists() else {}
            except Exception:
                existing = {}
            existing[alias] = name
            alias_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        self.server._refresh_llm_skill_tools()

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "name": name,
                    "path": str(csv_path),
                    "alias": alias or None,
                    "description": description or None,
                    "expression": expression or None,
                    "expression_preset": expression_preset or None,
                    "recordings": self._list_recordings(),
                },
            }
        )

    async def api_recording_aliases(self, request: Request) -> JSONResponse:
        import json

        path = Path(self.server.config.recordings_dir) / "aliases.json"
        if request.method == "GET":
            if not path.exists():
                return JSONResponse({"ok": True, "result": {"aliases": {}}})
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            return JSONResponse({"ok": True, "result": {"aliases": data if isinstance(data, dict) else {}}})

        body = await request.json()
        aliases = body.get("aliases")
        if not isinstance(aliases, dict):
            return JSONResponse({"ok": False, "error": "aliases must be an object"}, status_code=400)
        normalized = {str(k).strip(): str(v).strip() for k, v in aliases.items() if str(k).strip() and str(v).strip()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return JSONResponse({"ok": True, "result": {"aliases": normalized}})

    async def api_expressions(self, request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "expressions": self._list_expressions(),
                    "expression_catalog": self._list_expression_catalog(),
                    "eyes": list_eyes(),
                    "led_effects": list_led_effects(),
                    "presets": list_expression_presets(),
                },
            }
        )

    async def api_eyes(self, request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "eyes": list_eyes(),
                    "capacity": expression_capabilities()["eyes"],
                    "schema": expression_schemas()["eye_clip"],
                },
            }
        )

    async def api_eye_source(self, request: Request) -> FileResponse | JSONResponse:
        eye_id = str(request.path_params.get("eye_id") or "")
        try:
            path = eye_source_path(eye_id)
        except (ExpressionLibraryError, ExpressionClipError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        return FileResponse(path)

    async def api_eye_update(self, request: Request) -> JSONResponse:
        eye_id = str(request.path_params.get("eye_id") or "")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ExpressionLibraryError("eye update body must be an object")
            eye = set_eye_default_led(eye_id, body.get("default_led_effect_id"))
        except (ExpressionLibraryError, ExpressionClipError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "result": {"eye": eye}})

    async def api_eye_sync(self, request: Request) -> JSONResponse:
        eye_id = str(request.path_params.get("eye_id") or "")
        return await self._sync_eye_response(eye_id)

    async def api_led_effects(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            return JSONResponse(
                {
                    "ok": True,
                    "result": {
                        "led_effects": list_led_effects(),
                        "capacity": expression_capabilities()["led_effects"],
                        "schema": expression_schemas()["led_effect"],
                    },
                }
            )
        try:
            body = await request.json()
            effect = save_led_effect(body)
        except (ExpressionLibraryError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "result": {"led_effect": effect}})

    async def api_expression_presets(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            return JSONResponse(
                {
                    "ok": True,
                    "result": {
                        "presets": list_expression_presets(),
                        "capacity": expression_capabilities()["presets"],
                        "schema": expression_schemas()["expression_preset"],
                    },
                }
            )
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ExpressionLibraryError("preset body must be an object")
            if body.get("confirmed") is not True:
                raise ExpressionLibraryError("saving a preset requires confirmed=true")
            preset = save_expression_preset(body)
        except (ExpressionLibraryError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "result": {"preset": preset}})

    async def api_expression_preview(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            composition = resolve_expression(body)
        except (ExpressionLibraryError, ExpressionClipError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        duration_ms = int(composition["duration_ms"])
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "composition": composition,
                    "preview": {
                        "duration_ms": duration_ms,
                        "phase_start": 0.0,
                        "phase_end": 1.0,
                        "channels_start_together": True,
                    },
                },
            }
        )

    async def _play_resolved_expression(self, composition: dict[str, Any]) -> tuple[int, Any]:
        if not self.server.esp32:
            return 503, {"ok": False, "error": "no_device"}
        effect = composition.get("led_effect") or {}
        payload: dict[str, Any] = {
            "eye_clip_id": composition.get("eye_storage_clip_id"),
            "led_effect_id": composition.get("led_effect_id"),
            "led_params": composition.get("led_params") or {},
            "playback": composition.get("playback") or "once",
            "duration_ms": int(composition.get("duration_ms") or 3000),
        }
        if composition.get("preset_id"):
            payload["preset_id"] = composition["preset_id"]
        if effect.get("mode") is not None:
            payload["led_mode"] = int(effect["mode"])
        if isinstance(effect.get("program"), dict):
            payload["led_program"] = effect["program"]
        payload = self.server.esp32.with_owner_auth(payload, reason="expression_play")
        status, response, _ = await self.server.esp32.proxy_post("/device/expressions/play", payload)
        return status, response

    async def api_expression_play(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            composition = resolve_expression(body)
        except (ExpressionLibraryError, ExpressionClipError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        status, device_body = await self._play_resolved_expression(composition)
        ok = status < 400 and not (isinstance(device_body, dict) and device_body.get("ok") is False)
        return JSONResponse(
            {"ok": ok, "result": {"composition": composition, "device": device_body}},
            status_code=status,
        )

    async def api_expression_stop(self, request: Request) -> JSONResponse:
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        payload = self.server.esp32.with_owner_auth({}, reason="expression_stop")
        status, body, _ = await self.server.esp32.proxy_post("/device/expressions/stop", payload)
        return JSONResponse(body if isinstance(body, dict) else {"ok": False, "raw": str(body)}, status_code=status)

    async def api_expression_capabilities(self, request: Request) -> JSONResponse:
        local = expression_capabilities()
        device: Any = None
        if self.server.esp32:
            try:
                status, body, _ = await self.server.esp32.proxy_get("/device/expression-capabilities")
                if status < 400 and isinstance(body, dict):
                    device = body.get("result") or body
            except Exception:
                logger.exception("expression_capabilities.device_failed")
        return JSONResponse({"ok": True, "result": {"library": local, "device": device}})

    async def api_expression_clips(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            return JSONResponse({"ok": True, "result": {"clips": list_expression_clips()}})

        try:
            payload = await self._read_expression_clip_upload(request)
            manifest = create_expression_clip(**payload)
        except ExpressionClipError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            logger.exception("expression_clip.create_failed")
            return JSONResponse({"ok": False, "error": f"clip conversion failed: {exc}"}, status_code=500)

        return JSONResponse({"ok": True, "result": {"clip": manifest}})

    async def _read_expression_clip_upload(self, request: Request) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            body = await request.json()
            if not isinstance(body, dict):
                raise ExpressionClipError("JSON body must be an object")
            encoded = str(body.get("content_base64") or "")
            if not encoded:
                raise ExpressionClipError("content_base64 is required for JSON uploads")
            try:
                source_bytes = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ExpressionClipError("content_base64 is invalid") from exc
            return {
                "clip_id": str(body.get("clip_id") or body.get("expression") or ""),
                "expression": str(body.get("expression") or body.get("clip_id") or ""),
                "source_bytes": source_bytes,
                "filename": str(body.get("filename") or "upload.bin"),
                "content_type": str(body.get("content_type") or ""),
                "fps": int(body.get("fps") or 10),
                "duration_s": float(body["duration_s"]) if body.get("duration_s") is not None else None,
                "grid_rows": int(body["grid_rows"]) if body.get("grid_rows") is not None else None,
                "grid_cols": int(body["grid_cols"]) if body.get("grid_cols") is not None else None,
                "default_led_effect_id": str(body.get("default_led_effect_id") or "") or None,
            }

        query = request.query_params
        source_bytes = await request.body()
        filename = query.get("filename") or "upload.bin"
        return {
            "clip_id": query.get("clip_id") or query.get("expression") or "",
            "expression": query.get("expression") or query.get("clip_id") or "",
            "source_bytes": source_bytes,
            "filename": filename,
            "content_type": content_type,
            "fps": int(query.get("fps") or 10),
            "duration_s": float(query["duration_s"]) if query.get("duration_s") else None,
            "grid_rows": int(query["grid_rows"]) if query.get("grid_rows") else None,
            "grid_cols": int(query["grid_cols"]) if query.get("grid_cols") else None,
            "default_led_effect_id": query.get("default_led_effect_id") or None,
        }

    async def api_camera_snap(self, request: Request) -> JSONResponse:
        camera = self._make_camera_capture()
        if not camera.enabled:
            return JSONResponse({"ok": False, "error": "camera_disabled", "result": {"device": camera.device_label}})
        data_url = camera.capture_data_url()
        if not data_url:
            return JSONResponse({"ok": False, "error": "capture_failed", "result": {"device": camera.device_label}})
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "device": camera.device_label,
                    "data_url": data_url,
                },
            }
        )

    def _make_camera_capture(self) -> CameraCapture:
        return CameraCapture(
            self.server.config.camera,
            device_esp32_config=self.server.config.device_esp32,
            esp32_manager=self.server.esp32,
        )

    @staticmethod
    async def _request_json_object(request: Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    async def api_sensor_context(self, request: Request) -> JSONResponse:
        camera = self._make_camera_capture()
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "device": {
                        "lamp_id": self.server.config.device.lamp_id,
                    },
                    "camera": {
                        "enabled": camera.enabled,
                        "device": camera.device_label,
                    },
                    "voice": {
                        "enabled": self.server._wake_loop is not None,
                        "stt_provider": self.server.config.voice.stt_provider,
                        "tts_provider": self.server.config.voice.tts_provider,
                        "vad_enabled": bool(self.server.config.voice.vad_enabled),
                    },
                },
            }
        )

    async def api_agent_ask(self, request: Request) -> JSONResponse:
        body = await self._request_json_object(request)
        if body is None:
            return JSONResponse({"ok": False, "error": "invalid_json_body"}, status_code=400)
        question = str(body.get("question") or "").strip()
        if not question:
            return JSONResponse({"ok": False, "error": "question_required"}, status_code=400)
        options = body.get("options") or []
        if not isinstance(options, list):
            options = []
        options = [str(item) for item in options if str(item).strip()]
        request_id = str(body.get("request_id") or "").strip()
        try:
            timeout_s = float(body.get("timeout_s", 120.0))
        except (TypeError, ValueError):
            timeout_s = 120.0
        timeout_s = max(5.0, min(600.0, timeout_s))
        result = await self.server.agent_ask_user(
            question=question,
            options=options,
            request_id=request_id,
            timeout_s=timeout_s,
        )
        return JSONResponse({"ok": True, "result": result})

    async def api_agent_ask_reply(self, request: Request) -> JSONResponse:
        body = await self._request_json_object(request)
        if body is None:
            return JSONResponse({"ok": False, "error": "invalid_json_body"}, status_code=400)
        ask_id = str(body.get("ask_id") or "").strip()
        reply = str(body.get("reply") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        if not ask_id or not reply:
            return JSONResponse({"ok": False, "error": "ask_id_and_reply_required"}, status_code=400)
        ok = await self.server.agent_reply_user(ask_id=ask_id, reply=reply, request_id=request_id)
        return JSONResponse({"ok": ok, "result": {"accepted": ok}})

    async def api_agent_callback(self, request: Request) -> JSONResponse:
        body = await self._request_json_object(request)
        if body is None:
            return JSONResponse({"ok": False, "error": "invalid_json_body"}, status_code=400)
        # Free-form status payload from an external agent provider.
        status = body.get("status")
        detail = body.get("detail")
        request_id = str(body.get("request_id") or "").strip()
        if status:
            await self.server.events.publish(
                ChatMessage(role="assistant", content=f"[Codex] {status}: {detail or ''}".strip(), request_id=request_id)
            )
        return JSONResponse({"ok": True, "result": {"accepted": True}})

    async def api_agent_tasks(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "result": {"agent_tasks": self.server.agent.list_tasks()}})

    async def api_agent_health(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "result": await self.server.agent.refresh_health()})

    async def api_livekit_token(self, request: Request) -> JSONResponse:
        """Proxy token requests to the managed Lampgo LiveKit Agent SDK."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        user_identity = str(body.get("user_identity") or f"lampgo-web-{uuid.uuid4().hex[:8]}")
        voice_agent = str(body.get("voice_agent") or "lampgo-jarvis")
        requested_room = str(body.get("room_name") or "").strip()
        if requested_room:
            logger.info("web.livekit_token_custom_room_ignored", requested_room=requested_room)
        if not body.get("client_call_id"):
            logger.info("web.livekit_token_legacy_client_rejected", user_identity=user_identity)
            return JSONResponse(
                {"ok": False, "error": "please refresh the web UI before starting a call"},
                status_code=409,
            )
        client_call_id = str(body.get("client_call_id"))
        reason = str(body.get("reason") or "")
        logger.info(
            "web.livekit_token_requested",
            user_identity=user_identity,
            voice_agent=voice_agent,
            client_call_id=client_call_id,
            reason=reason,
        )
        async with self._livekit_token_lock:
            now = time.monotonic()
            if now < self._livekit_token_gate_until and self._livekit_token_gate_owner != client_call_id:
                logger.info(
                    "web.livekit_token_deduped",
                    owner=self._livekit_token_gate_owner,
                    requester=client_call_id,
                )
                return JSONResponse({"ok": False, "error": "another call is already starting"}, status_code=409)
            self._livekit_token_gate_until = now + 3.0
            self._livekit_token_gate_owner = client_call_id
        try:
            async with self._livekit_room_lock:
                return await self._issue_livekit_token_locked(
                    user_identity=user_identity,
                    voice_agent=voice_agent,
                    client_call_id=client_call_id,
                    reason=reason,
                )
        except Exception as exc:
            async with self._livekit_token_lock:
                if self._livekit_token_gate_owner == client_call_id:
                    self._livekit_token_gate_until = 0.0
                    self._livekit_token_gate_owner = ""
            logger.exception("web.livekit_token_failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)

    async def _issue_livekit_token_locked(
        self,
        *,
        user_identity: str,
        voice_agent: str,
        client_call_id: str,
        reason: str,
    ) -> JSONResponse:
        from lampgo.voice.agent_sdk import AGENT_SDK_PORT

        room_name = ""
        for active_name, active_room in self._livekit_active_rooms.items():
            active_owner = str((active_room or {}).get("client_call_id") or "")
            if active_owner == client_call_id:
                room_name = active_name
                break
            if active_owner:
                async with self._livekit_token_lock:
                    if self._livekit_token_gate_owner == client_call_id:
                        self._livekit_token_gate_until = 0.0
                        self._livekit_token_gate_owner = ""
                logger.info(
                    "web.livekit_token_rejected_active_call",
                    room=active_name,
                    owner=active_owner,
                    requester=client_call_id,
                    reason=reason,
                )
                return JSONResponse({"ok": False, "error": "another call is already active"}, status_code=409)
        if not room_name:
            room_name = f"lampgo-{uuid.uuid4().hex[:12]}"

        await self._close_existing_livekit_rooms(
            keep_room=room_name,
            reason=f"new_{reason or 'call'}",
            client_call_id=client_call_id,
        )
        agent_sdk = getattr(self.server, "_agent_sdk", None)
        ensure_ready = getattr(self.server, "ensure_agent_sdk_ready", None)
        if callable(ensure_ready):
            ready, error = await ensure_ready(timeout_s=10.0)
        else:
            wait_ready = getattr(agent_sdk, "wait_ready", None)
            ready = bool(callable(wait_ready) and await wait_ready(timeout_s=10.0))
            error = "" if ready else "voice agent SDK is not running"
        if not ready:
            logger.info(
                "web.livekit_token_agent_not_ready",
                room=room_name,
                user_identity=user_identity,
                voice_agent=voice_agent,
                client_call_id=client_call_id,
                error=error,
            )
            async with self._livekit_token_lock:
                if self._livekit_token_gate_owner == client_call_id:
                    self._livekit_token_gate_until = 0.0
                    self._livekit_token_gate_owner = ""
            return JSONResponse(
                {"ok": False, "error": error or "voice agent SDK is not ready"},
                status_code=503,
            )
        async with httpx_module.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.post(
                f"http://127.0.0.1:{AGENT_SDK_PORT}/rtc/token",
                json={
                    "room_name": room_name,
                    "user_identity": user_identity,
                    "voice_agent": voice_agent,
                },
            )
            resp.raise_for_status()
            result = resp.json()
        self._livekit_active_rooms = {
            room_name: {
                "client_call_id": client_call_id,
                "reason": reason,
                "user_identity": user_identity,
                "created_at": time.monotonic(),
            }
        }
        logger.info(
            "web.livekit_room_active",
            room=room_name,
            client_call_id=client_call_id,
            reason=reason,
        )
        return JSONResponse({"ok": True, "result": result})

    async def api_livekit_room_end(self, request: Request) -> JSONResponse:
        """Hard-close a LiveKit room when the browser ends a call."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        room_name = str(body.get("room_name") or "").strip()
        reason = str(body.get("reason") or "browser_end").strip() or "browser_end"
        client_call_id = str(body.get("client_call_id") or "").strip()
        rooms = [room_name] if room_name else list(self._livekit_active_rooms)
        async with self._livekit_room_lock:
            closed = await self._delete_livekit_rooms(
                rooms,
                reason=reason,
                client_call_id=client_call_id,
            )
        return JSONResponse({"ok": True, "result": {"closed_rooms": closed}})

    def _is_managed_livekit_room(self, room_name: str) -> bool:
        room = str(room_name or "").strip()
        if not room:
            return False
        configured = "lampgo"
        return room == configured or room.startswith(f"{configured}-") or room.startswith("lampgo-")

    async def _close_existing_livekit_rooms(
        self,
        *,
        keep_room: str,
        reason: str,
        client_call_id: str,
    ) -> list[str]:
        """Close every managed LiveKit room except ``keep_room`` before a new call starts."""
        rooms: set[str] = {
            name
            for name in self._livekit_active_rooms
            if name and name != keep_room and self._is_managed_livekit_room(name)
        }

        closed = await self._delete_livekit_rooms(
            sorted(rooms),
            reason=reason,
            client_call_id=client_call_id,
        )
        if closed:
            logger.info(
                "web.livekit_rooms_closed_before_start",
                keep_room=keep_room,
                closed_rooms=closed,
                reason=reason,
                client_call_id=client_call_id,
            )
        return closed

    async def _delete_livekit_rooms(
        self,
        rooms: list[str] | set[str] | tuple[str, ...],
        *,
        reason: str,
        client_call_id: str = "",
    ) -> list[str]:
        """Delete LiveKit rooms and forget them from the local active-room registry."""
        targets = sorted(
            {
                str(room or "").strip()
                for room in rooms
                if self._is_managed_livekit_room(str(room or "").strip())
            }
        )
        if not targets:
            return []
        for room in targets:
            self._livekit_active_rooms.pop(room, None)
        logger.info(
            "web.livekit_room_admin_close_skipped_cloud_auth",
            rooms=targets,
            reason=reason,
            client_call_id=client_call_id,
        )
        return []

    async def api_agent_cancel(self, request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        ok = await self.server.agent.cancel_task(task_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "task not found or already stopped"}, status_code=404)
        return JSONResponse({"ok": True, "result": {"agent_task": self.server.agent.get_task(task_id)}})

    # ---- user-editable config / persona / memory ----

    # Each preset exposes ``api_urls`` keyed by message_type so the frontend
    # can re-derive Base URL from the (provider, message_type) pair — most
    # modern providers publish both an OpenAI-compatible endpoint and an
    # Anthropic-compatible one on different paths, so baking the URL into
    # the provider alone is wrong.  ``default_message_type`` is what we
    # switch to when the user first picks this provider.
    #
    # Legacy top-level ``base_url`` / ``message_type`` are kept because
    # older server-side code paths (and pinned tests) still read them;
    # they mirror the entry at ``api_urls[default_message_type]``.
    _PROVIDER_PRESETS = {
        "mimo": {
            "label": "MiMo",
            # MiMo 在两个独立端点上分别暴露两种协议：
            #   OpenAI   : https://api.xiaomimimo.com/v1/chat/completions
            #   Anthropic: https://api.xiaomimimo.com/anthropic/v1/messages
            # 鉴权：官方 curl 用 `api-key` 头；社区 SDK 文档里又有 `Bearer`。
            # 我们在 Anthropic 路径上把 x-api-key / api-key / Bearer 三个
            # 一起发，所以同一把 key 两个端点都能通。
            "api_urls": {
                "openai": "https://api.xiaomimimo.com/v1",
                "anthropic": "https://api.xiaomimimo.com/anthropic/v1",
            },
            "default_message_type": "openai",
            # mimo-v2.5：通用新一代模型，同时作为 agent 主模型和 fast_model（摘要/意图）。
            # 如需分工：主模型可选 mimo-v2-omni（强推理），fast_model 建议保持非推理模型
            # （mimo-v2.5 / mimo-v2-pro），避免推理模型把预算花在思考链上导致空返。
            "default_model": "mimo-v2.5",
            "default_fast_model": "mimo-v2.5",
            # legacy mirrors (see comment above)
            "base_url": "https://api.xiaomimimo.com/v1",
            "message_type": "openai",
        },
        "openrouter": {
            "label": "OpenRouter",
            # OpenRouter 同一 base 既暴露 OpenAI 的 /chat/completions
            # 又暴露 Anthropic 的 /messages（按你发的 tool 请求路由模型）。
            "api_urls": {
                "openai": "https://openrouter.ai/api/v1",
                "anthropic": "https://openrouter.ai/api/v1",
            },
            "default_message_type": "openai",
            "default_model": "anthropic/claude-3.5-sonnet",
            "default_fast_model": "anthropic/claude-3.5-haiku",
            "base_url": "https://openrouter.ai/api/v1",
            "message_type": "openai",
        },
        "anthropic": {
            "label": "Anthropic",
            # 真 Anthropic 只提供 Messages API；官方没有 OpenAI 兼容层。
            "api_urls": {
                "anthropic": "https://api.anthropic.com/v1",
            },
            "default_message_type": "anthropic",
            "default_model": "claude-sonnet-4-20250514",
            "default_fast_model": "claude-haiku-4-20250514",
            "base_url": "https://api.anthropic.com/v1",
            "message_type": "anthropic",
        },
        "openai": {
            "label": "OpenAI",
            # OpenAI 官方不提供 Anthropic 兼容端点。
            "api_urls": {
                "openai": "https://api.openai.com/v1",
            },
            "default_message_type": "openai",
            "default_model": "gpt-4o-mini",
            "default_fast_model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "message_type": "openai",
        },
        "deepseek": {
            "label": "DeepSeek",
            "api_urls": {
                "openai": "https://api.deepseek.com/v1",
            },
            "default_message_type": "openai",
            "default_model": "deepseek-chat",
            "default_fast_model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "message_type": "openai",
        },
        "google": {
            "label": "Google Gemini",
            # Google 的 OpenAI 兼容层，原生 Gemini REST 另算（这里不暴露）。
            "api_urls": {
                "openai": "https://generativelanguage.googleapis.com/v1beta/openai",
            },
            "default_message_type": "openai",
            "default_model": "gemini-2.5-flash",
            "default_fast_model": "gemini-2.5-flash",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "message_type": "openai",
        },
        "ollama": {
            "label": "Ollama（本地）",
            "api_urls": {
                "openai": "http://127.0.0.1:11434/v1",
            },
            "default_message_type": "openai",
            "default_model": "qwen2.5:7b-instruct",
            "default_fast_model": "qwen2.5:7b-instruct",
            "base_url": "http://127.0.0.1:11434/v1",
            "message_type": "openai",
        },
        "custom": {
            "label": "自定义",
            # 自定义时不预置 URL，让前端把 Base URL 留给用户自己填。
            "api_urls": {},
            "default_message_type": "openai",
            "default_model": "",
            "default_fast_model": "",
            "base_url": "",
            "message_type": "openai",
        },
    }

    # ------------------------------------------------------------------
    # Generic configuration endpoints (device / voice / motion / safety)
    # ------------------------------------------------------------------

    # Fields that require a daemon restart to take effect.
    #
    # Deliberately NOT listed here:
    #   - device.motor_port → hot-reconnected by `server.reload_motor_runtime`.
    #   - camera.port      → hot-swapped via the set_camera WS command in
    #                        `server._handle_set_camera`; the Web UI also
    #                        rebroadcasts this on save.
    #   - voice.mic_device → hot-swapped via set_mic / WakeLoop.set_mic_device.
    _COLD_RESTART_FIELDS: frozenset[str] = frozenset(
        {
            "device.lamp_id",
            "device.use_degrees",
            "led.baud_rate",
            "web.host",
            "web.port",
            "socket_path",
        }
    )

    _VOICE_HOT_RELOAD_FIELDS: frozenset[str] = frozenset(
        {
            "voice.wake_word",
            "voice.tts_provider",
            "voice.tts_model",
            "voice.tts_voice",
            "voice.call_mode",
            "voice.livekit_allow_interruptions",
            "voice.echo_gate_hangover_ms",
            "voice.echo_text_filter_enabled",
            "voice.volcengine_app_id",
            "voice.volcengine_access_token",
        }
    )

    # Map web UI section → (allowed LampgoConfig paths, restart-only fields).
    # Each path uses the same dotted notation as the provenance map.
    _SECTION_FIELDS: dict[str, tuple[str, ...]] = {
        "device": (
            "device.motor_port",
            "device.lamp_id",
            "device.use_degrees",
        ),
        "voice": (
            "voice.stt_provider",
            "voice.stt_model",
            "voice.tts_provider",
            "voice.tts_model",
            "voice.tts_voice",
            "voice.mic_device",
            "camera.port",
            "voice.wake_word",
            "voice.call_mode",
            "voice.livekit_allow_interruptions",
            "voice.echo_gate_hangover_ms",
            "voice.echo_text_filter_enabled",
            "voice.silence_timeout_s",
            "voice.volcengine_app_id",
            "voice.volcengine_access_token",
        ),
        "motion": (
            "motion.tick_rate_hz",
            "motion.default_max_velocity",
            "motion.default_style",
            "motion.default_playback_mode",
            "motion.idle_sway_enabled",
            "motion.idle_sway_idle_after_s",
            "motion.idle_sway_interval_s",
            "motion.idle_sway_interval_jitter_s",
            "motion.idle_sway_duration_s",
            "motion.idle_sway_amplitude",
            "motion.idle_sway_period_s",
            "motion.overlapping_action",
            "motion.anticipation_enabled",
            "motion.anticipation_threshold",
            "motion.anticipation_ratio",
            "motion.anticipation_duration_ms",
        ),
        "safety": (
            "safety.max_velocity",
            "safety.max_acceleration",
        ),
        "web": ("web.port",),
        "device_esp32": (
            "device_esp32.enabled",
            "device_esp32.preferred_host",
            "device_esp32.jpeg_quality",
            "device_esp32.framesize",
            "device_esp32.mic_enabled",
            "device_esp32.http_timeout_s",
        ),
    }

    def _dump_section(self, section_fields: tuple[str, ...], provenance: dict[str, str]) -> dict[str, Any]:
        """Build ``{field: {value, source}}`` payload for the given dotted paths."""
        cfg = self.server.config
        out: dict[str, Any] = {}
        for dotted in section_fields:
            head, _, tail = dotted.partition(".")
            obj = getattr(cfg, head, None)
            if not tail:
                value = obj
            else:
                value = getattr(obj, tail, None) if obj is not None else None
            if dotted in _SENSITIVE_CONFIG_FIELDS and value:
                from lampgo import personastore

                value = personastore.mask_api_key(str(value))
            out[dotted] = {
                "value": value,
                "source": provenance.get(dotted, "default"),
            }
        return out

    def _list_env_overrides(self, provenance: dict[str, str]) -> list[str]:
        return sorted(k for k, v in provenance.items() if v == "env")

    @staticmethod
    def _normalize_voice_call_mode(value: Any) -> str:
        mode = str(value or "stable").strip().lower().replace("-", "_")
        aliases = {
            "safe": "stable",
            "half_duplex": "stable",
            "barge_in": "interruptible",
            "interrupt": "interruptible",
            "interruptions": "interruptible",
            "aec": "esp32_aec",
            "experimental_aec": "esp32_aec",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"stable", "interruptible", "esp32_aec"}:
            raise ValueError("voice.call_mode must be stable, interruptible, or esp32_aec")
        return mode

    def _derive_voice_mode_fields(self, flat: dict[str, Any]) -> None:
        """Keep the product-level call mode as the source of truth.

        The old raw ``livekit_allow_interruptions`` switch is still persisted
        for the Agent SDK, but whenever the user saves ``voice.call_mode`` we
        derive it from the mode so the UI and runtime cannot drift.
        """
        if "voice.call_mode" in flat:
            mode = self._normalize_voice_call_mode(flat["voice.call_mode"])
            flat["voice.call_mode"] = mode
            flat["voice.livekit_allow_interruptions"] = mode in {"interruptible", "esp32_aec"}
        if "voice.echo_gate_hangover_ms" in flat and flat["voice.echo_gate_hangover_ms"] is not None:
            hangover = int(flat["voice.echo_gate_hangover_ms"])
            if hangover < 0 or hangover > 5000:
                raise ValueError("voice.echo_gate_hangover_ms must be between 0 and 5000")
            flat["voice.echo_gate_hangover_ms"] = hangover

    @staticmethod
    def _validate_safety_fields(flat: dict[str, Any]) -> None:
        for key, limit in (
            ("safety.max_velocity", _SAFETY_MAX_VELOCITY_LIMIT),
            ("safety.max_acceleration", _SAFETY_MAX_ACCELERATION_LIMIT),
        ):
            if key not in flat:
                continue
            try:
                value = float(flat[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number") from exc
            if value <= 0 or value > limit:
                raise ValueError(f"{key} must be > 0 and <= {limit:g}")
            flat[key] = value

    async def api_config_all(self, request: Request) -> JSONResponse:
        """Return every editable field grouped by tab, with per-field provenance."""
        from lampgo.core.config import load_config_with_provenance

        _, provenance = load_config_with_provenance()

        sections = {name: self._dump_section(fields, provenance) for name, fields in self._SECTION_FIELDS.items()}

        cold_fields = sorted(self._COLD_RESTART_FIELDS)

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "sections": sections,
                    "env_overrides": self._list_env_overrides(provenance),
                    "cold_restart_fields": cold_fields,
                    "provenance": provenance,
                },
            }
        )

    async def _save_section(
        self,
        request: Request,
        section: str,
    ) -> JSONResponse:
        """Common POST handler: accept a flat field map, validate + persist."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)

        allowed = self._SECTION_FIELDS.get(section)
        if allowed is None:
            return JSONResponse({"ok": False, "error": f"unknown section: {section}"}, status_code=400)
        allowed_set = set(allowed)

        # Accept two input shapes:
        #   { "device.motor_port": "/dev/...", ... }       ← dotted flat
        #   { "motor_port": "/dev/...", ... }              ← bare field names
        # We normalize to dotted form and reject unknown keys.
        flat: dict[str, Any] = {}
        for key, value in body.items():
            if key in allowed_set:
                flat[key] = value
                continue
            # try prefixing with section
            candidate = f"{section}.{key}"
            if candidate in allowed_set:
                flat[candidate] = value
                continue
            # try matching any allowed field with the same tail
            matches = [p for p in allowed_set if p.endswith("." + key)]
            if len(matches) == 1:
                flat[matches[0]] = value
                continue
            return JSONResponse(
                {"ok": False, "error": f"field '{key}' not allowed in section '{section}'"},
                status_code=400,
            )

        if not flat:
            return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)
        try:
            if section == "voice":
                self._derive_voice_mode_fields(flat)
            if section == "safety":
                self._validate_safety_fields(flat)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": f"invalid value: {exc}"}, status_code=400)

        # Build nested patch for personastore.
        patch: dict[str, Any] = {}
        for dotted, value in flat.items():
            head, _, tail = dotted.partition(".")
            if not tail:
                patch[head] = value
            else:
                patch.setdefault(head, {})[tail] = value

        # Apply to running config + save.
        from lampgo import personastore

        try:
            self._apply_flat_to_config(flat)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": f"invalid value: {exc}"}, status_code=400)
        personastore.patch_overrides_toml(patch)
        if section in {"device", "voice", "motion", "safety", "web", "device_esp32"}:
            logger.info(
                "web.config_saved",
                section=section,
                fields=sorted(flat.keys()),
                client=getattr(getattr(request, "client", None), "host", ""),
            )

        needs_restart = sorted(f for f in flat if f in self._COLD_RESTART_FIELDS)

        if any(f in self._VOICE_HOT_RELOAD_FIELDS for f in flat):
            asyncio.create_task(self.server.restart_voice_loop())

        if "voice.mic_device" in flat and self.server._wake_loop is not None:
            await self.server._wake_loop.set_mic_device(str(self.server.config.voice.mic_device or ""))

        # Recompute provenance so the UI can refresh the override hints.
        from lampgo.core.config import load_config_with_provenance

        _, provenance = load_config_with_provenance()

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "saved": sorted(flat.keys()),
                    "needs_restart": needs_restart,
                    "section": self._dump_section(allowed, provenance),
                    "env_overrides": self._list_env_overrides(provenance),
                },
            }
        )

    def _apply_flat_to_config(self, flat: dict[str, Any]) -> None:
        """Mutate ``self.server.config`` in place so hot-reload fields take effect."""
        cfg = self.server.config
        for dotted, value in flat.items():
            head, _, tail = dotted.partition(".")
            obj = getattr(cfg, head, None)
            if obj is None:
                continue
            if not tail:
                setattr(cfg, head, value)
                continue
            # Coerce to the current field's type for pydantic friendliness.
            current = getattr(obj, tail, None)
            coerced = _coerce_value(current, value)
            if head == "voice" and tail == "tts_voice":
                from lampgo.voice.tts import _volcengine_voice_or_default

                coerced = _volcengine_voice_or_default(str(coerced or ""))
            setattr(obj, tail, coerced)

    async def api_config_device(self, request: Request) -> JSONResponse:
        response = await self._save_section(request, "device")
        if response.status_code != 200:
            return response
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except Exception:
            return response
        saved = set((payload.get("result") or {}).get("saved") or [])
        if "device.motor_port" in saved:
            reload_result = await self.server.reload_motor_runtime()
            payload.setdefault("result", {}).setdefault("hot_reload", {})["device.motor_port"] = reload_result
        return JSONResponse(payload, status_code=response.status_code)

    async def api_config_voice(self, request: Request) -> JSONResponse:
        result = await self._save_section(request, "voice")
        try:
            from lampgo.voice.stt import build_stt
            self.server._stt = build_stt(self.server.config)
        except Exception:
            logger.exception("web.stt_rebuild_failed")
        return result

    async def api_config_motion(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "motion")

    async def api_config_safety(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "safety")

    async def api_config_web(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "web")

    async def api_config_device_esp32(self, request: Request) -> JSONResponse:
        """POST /api/config/device-esp32 — save ESP32 device preferences.

        Extra-behavioral: when ``enabled`` flips true we start mDNS discovery
        (and kill it when flipped back to false) so the Web UI doesn't need to
        wait for a daemon restart just to toggle the switch.
        """
        was_enabled = bool(self.server.config.device_esp32.enabled)
        response = await self._save_section(request, "device_esp32")
        try:
            new_cfg = self.server.config.device_esp32
            self.server.esp32.update_config(new_cfg)
            if new_cfg.enabled and not was_enabled:
                await self.server.esp32.start()
            elif not new_cfg.enabled and was_enabled:
                await self.server.esp32.shutdown()
                self.server.esp32.reset_session()
        except Exception:
            logger.exception("web.device_esp32_toggle_failed")
        return response

    async def api_config_detect(self, request: Request) -> JSONResponse:
        """Run hardware autodetect on demand (used by the 硬件 tab button)."""
        try:
            from lampgo import autodetect

            # detect_ports may block on serial IO; run off the event loop.
            result = await asyncio.to_thread(autodetect.detect_ports)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True, "result": result})

    async def api_config_restart(self, request: Request) -> JSONResponse:
        """Cold-restart hint.

        We intentionally don't try to actually restart the daemon here — the
        user may or may not have launched lampgo under a supervisor. Returning
        a structured hint lets the UI explain the situation (see design plan).
        """
        import os

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "restarted": False,
                    "pid": os.getpid(),
                    "hint": (
                        "请在启动 lampgo 的终端按 Ctrl+C 退出当前进程，再重跑 " "`uv run lampgo run --web`。硬件相关字段需要重新打开串口才会生效。"
                    ),
                },
            }
        )

    async def api_config_llm(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        if request.method == "GET":
            cfg = self.server.config.llm
            provider = str(LLMConfig.normalize_provider_alias(cfg.provider) or "")
            overrides = personastore.get_overrides_toml()
            message_type = ((overrides.get("llm") or {}).get("message_type") if isinstance(overrides, dict) else None) or "openai"
            share_memory = bool(getattr(self.server.config, "share_codex_memory", True))
            return JSONResponse(
                {
                    "ok": True,
                    "result": {
                        "provider": provider,
                        "api_base": cfg.api_base,
                        "api_key_preview": personastore.mask_api_key(cfg.api_key),
                        "api_key_is_set": bool(cfg.api_key),
                        "model": cfg.model,
                        "fast_model": cfg.fast_model,
                        "message_type": message_type,
                        "max_tokens": cfg.max_tokens,
                        "summary_max_tokens": cfg.summary_max_tokens,
                        "context_window": cfg.context_window,
                        "temperature": cfg.temperature,
                        "timeout_s": cfg.timeout_s,
                        "history_turns": cfg.history_turns,
                        "enable_thinking": bool(cfg.enable_thinking),
                        "share_codex_memory": share_memory,
                        "provider_presets": self._PROVIDER_PRESETS,
                        # MiMo web search sub-service (see LLMConfig docstring).
                        "web_search_enabled": bool(cfg.web_search_enabled),
                        "web_search_force": bool(cfg.web_search_force),
                        "web_search_limit": cfg.web_search_limit,
                        "web_search_max_keyword": cfg.web_search_max_keyword,
                        "web_search_country": cfg.web_search_country,
                        "web_search_region": cfg.web_search_region,
                        "web_search_city": cfg.web_search_city,
                        "web_search_api_key_preview": personastore.mask_api_key(cfg.web_search_api_key),
                        "web_search_api_key_is_set": bool(cfg.web_search_api_key),
                    },
                }
            )

        body = await request.json()
        # Quick path: caller only toggles Codex summary sharing.
        if "share_codex_memory" in body and not any(
            k in body
            for k in (
                "provider",
                "api_base",
                "api_key",
                "model",
                "fast_model",
                "message_type",
                "max_tokens",
                "summary_max_tokens",
                "context_window",
                "temperature",
                "timeout_s",
                "history_turns",
                "enable_thinking",
                "web_search_enabled",
                "web_search_force",
                "web_search_limit",
                "web_search_max_keyword",
                "web_search_country",
                "web_search_region",
                "web_search_city",
                "web_search_api_key",
            )
        ):
            from lampgo import personastore

            share = bool(body.get("share_codex_memory"))
            personastore.patch_overrides_toml({"share_codex_memory": share})
            self.server.config.share_codex_memory = share
            self._invalidate_persona_cache()
            return JSONResponse({"ok": True, "result": {"share_codex_memory": share}})

        validate = bool(body.get("validate", True))
        dry_run = bool(body.get("dry_run", False))
        # PATCH-like semantics: when the client omits a key from the body,
        # keep the current value.  This matters because legacy/API callers
        # may still save a subset of web_search_* fields and MUST NOT clobber
        # the main LLM's api_base / message_type / fast_model.
        # The short-circuit at the top of this handler already covers the
        # share-memory-only POST;
        # every other partial-update path goes through here.
        current_llm = self.server.config.llm
        provider = str(body.get("provider") or "").strip() or current_llm.provider
        provider = str(LLMConfig.normalize_provider_alias(provider) or "")
        api_base = str(body.get("api_base") or "").strip() if "api_base" in body else current_llm.api_base
        api_key_raw = body.get("api_key")
        api_key = str(api_key_raw).strip() if api_key_raw is not None else ""
        model = str(body.get("model") or "").strip() or current_llm.model
        fast_model = str(body.get("fast_model") or "").strip() or (model if "model" in body else current_llm.fast_model)
        message_type = str(body.get("message_type") or "").strip() or (current_llm.message_type or "openai")
        share_memory = body.get("share_codex_memory")

        def _coerce_positive_int(raw: Any, fallback: int) -> int:
            if raw is None or raw == "":
                return int(fallback)
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return int(fallback)
            return v if v > 0 else int(fallback)

        def _coerce_temperature(raw: Any, fallback: float) -> float:
            if raw is None or raw == "":
                return float(fallback)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                return float(fallback)
            return max(0.0, min(2.0, v))

        def _coerce_timeout(raw: Any, fallback: float) -> float:
            if raw is None or raw == "":
                return float(fallback)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                return float(fallback)
            return max(5.0, min(600.0, v))

        # ``current_llm`` was already hoisted above for PATCH-like fallbacks.
        max_tokens_val = _coerce_positive_int(body.get("max_tokens"), current_llm.max_tokens)
        summary_max_tokens_val = _coerce_positive_int(body.get("summary_max_tokens"), current_llm.summary_max_tokens)
        context_window_val = _coerce_positive_int(body.get("context_window"), current_llm.context_window)
        temperature_val = _coerce_temperature(body.get("temperature"), current_llm.temperature)
        timeout_s_val = _coerce_timeout(body.get("timeout_s"), current_llm.timeout_s)

        def _coerce_history_turns(raw: Any, fallback: int) -> int:
            if raw is None or raw == "":
                return int(fallback)
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return int(fallback)
            return max(0, min(200, v))

        history_turns_val = _coerce_history_turns(body.get("history_turns"), current_llm.history_turns)

        def _opt_bool(raw: Any, fallback: bool) -> bool:
            if raw is None:
                return bool(fallback)
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)):
                return bool(raw)
            s = str(raw).strip().lower()
            if s in {"true", "1", "yes", "on"}:
                return True
            if s in {"false", "0", "no", "off", ""}:
                return False
            return bool(fallback)

        enable_thinking_val = _opt_bool(body.get("enable_thinking"), current_llm.enable_thinking)

        # ------------------------------------------------------------------
        # MiMo web search sub-service fields.
        #
        # These travel alongside the main LLM settings for backwards
        # compatibility with existing config files and API callers.
        # The frontend no longer exposes them: when ``provider == "mimo"``
        # web search defaults on and reuses the main MiMo key. The actual
        # search sub-service remains logically independent of the main LLM
        # path and always talks MiMo OpenAI-compat.
        # ------------------------------------------------------------------
        def _coerce_bounded_int(raw: Any, fallback: int, *, lo: int, hi: int) -> int:
            if raw is None or raw == "":
                return int(fallback)
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return int(fallback)
            return max(lo, min(hi, v))

        ws_enabled = _opt_bool(body.get("web_search_enabled"), current_llm.web_search_enabled)
        if provider == "mimo" and "web_search_enabled" not in body:
            ws_enabled = True
        ws_force = _opt_bool(body.get("web_search_force"), current_llm.web_search_force)
        ws_limit = _coerce_bounded_int(body.get("web_search_limit"), current_llm.web_search_limit, lo=1, hi=10)
        ws_max_keyword = _coerce_bounded_int(
            body.get("web_search_max_keyword"),
            current_llm.web_search_max_keyword,
            lo=1,
            hi=10,
        )
        ws_country = str(body.get("web_search_country") or current_llm.web_search_country).strip()
        ws_region = str(body.get("web_search_region") or current_llm.web_search_region).strip()
        ws_city = str(body.get("web_search_city") or current_llm.web_search_city).strip()

        ws_key_raw = body.get("web_search_api_key")
        ws_key_in = str(ws_key_raw).strip() if ws_key_raw is not None else ""
        if ws_key_in == "" or set(ws_key_in) <= {"•", "*"}:
            # empty / placeholder → keep existing.
            effective_ws_key = current_llm.web_search_api_key
        else:
            effective_ws_key = ws_key_in

        if api_key == "":
            # empty string is a no-op (keep existing); client sends new key only when rotating.
            effective_key = self.server.config.llm.api_key
        elif set(api_key) <= {"•", "*"}:
            effective_key = self.server.config.llm.api_key
        else:
            effective_key = api_key

        try:
            self._validated_llm_base(provider, api_base)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        if validate:
            probe_error = await self._probe_llm(
                provider=provider,
                api_base=api_base,
                api_key=effective_key,
                model=fast_model or model,
                message_type=message_type,
            )
            if probe_error:
                return JSONResponse({"ok": False, "error": probe_error}, status_code=400)
            if dry_run:
                return JSONResponse({"ok": True, "result": {"dry_run": True, "ping": "ok"}})

        patch: dict = {
            "llm": {
                "provider": provider,
                "api_base": api_base,
                "model": model,
                "fast_model": fast_model,
                "message_type": message_type,
                "max_tokens": max_tokens_val,
                "summary_max_tokens": summary_max_tokens_val,
                "context_window": context_window_val,
                "temperature": temperature_val,
                "timeout_s": timeout_s_val,
                "history_turns": history_turns_val,
                "enable_thinking": enable_thinking_val,
                "web_search_enabled": ws_enabled,
                "web_search_force": ws_force,
                "web_search_limit": ws_limit,
                "web_search_max_keyword": ws_max_keyword,
                "web_search_country": ws_country,
                "web_search_region": ws_region,
                "web_search_city": ws_city,
            },
        }
        if share_memory is not None:
            patch["share_codex_memory"] = bool(share_memory)
        personastore.patch_overrides_toml(patch)
        logger.info(
            "web.config_saved",
            section="llm",
            fields=sorted(patch.get("llm", {}).keys()),
            client=getattr(getattr(request, "client", None), "host", ""),
        )

        # Persist the effective key into credentials.json so it becomes the
        # durable source of truth (as the UI hint promises). This is
        # idempotent and covers three cases:
        #   1. user typed a brand-new key → store it
        #   2. user left field blank but a key already came from env → mirror
        #      it into credentials so users can later remove .env safely
        #   3. user left field blank and a key was already in credentials →
        #      no-op write (same value)
        if effective_key:
            existing = personastore.get_credentials().get("llm_api_key")
            if existing != effective_key:
                personastore.set_credentials({"llm_api_key": effective_key})

        # Web-search key lives in credentials.json under its own slot.
        # Only persist when the user actually typed a new key — an empty
        # or placeholder input must NOT overwrite an existing credential.
        if ws_key_in and not set(ws_key_in) <= {"•", "*"}:
            existing_ws = personastore.get_credentials().get("llm_web_search_api_key")
            if existing_ws != effective_ws_key:
                personastore.set_credentials({"llm_web_search_api_key": effective_ws_key})

        # Apply to running config and hot-reload LLM client.
        cfg = self.server.config
        cfg.llm.provider = provider
        cfg.llm.api_base = api_base
        cfg.llm.api_key = effective_key
        cfg.llm.model = model
        cfg.llm.fast_model = fast_model
        cfg.llm.max_tokens = max_tokens_val
        cfg.llm.summary_max_tokens = summary_max_tokens_val
        cfg.llm.context_window = context_window_val
        cfg.llm.temperature = temperature_val
        cfg.llm.timeout_s = timeout_s_val
        cfg.llm.history_turns = history_turns_val
        cfg.llm.enable_thinking = enable_thinking_val
        cfg.llm.web_search_enabled = ws_enabled
        cfg.llm.web_search_force = ws_force
        cfg.llm.web_search_limit = ws_limit
        cfg.llm.web_search_max_keyword = ws_max_keyword
        cfg.llm.web_search_country = ws_country
        cfg.llm.web_search_region = ws_region
        cfg.llm.web_search_city = ws_city
        cfg.llm.web_search_api_key = effective_ws_key
        if share_memory is not None:
            cfg.share_codex_memory = bool(share_memory)

        try:
            self.server.reload_llm_client()
        except Exception:
            logger.exception("web.reload_llm_failed")

        # Invalidate persona/memory cache so the next prompt assembly re-reads
        try:
            from lampgo.persona.bundle import invalidate_bundles

            invalidate_bundles()
        except Exception:
            pass

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "provider": provider,
                    "api_base": api_base,
                    "model": model,
                    "fast_model": fast_model,
                    "message_type": message_type,
                    "max_tokens": max_tokens_val,
                    "summary_max_tokens": summary_max_tokens_val,
                    "context_window": context_window_val,
                    "temperature": temperature_val,
                    "timeout_s": timeout_s_val,
                    "history_turns": history_turns_val,
                    "enable_thinking": enable_thinking_val,
                    "api_key_preview": personastore.mask_api_key(effective_key),
                    "api_key_is_set": bool(effective_key),
                    "web_search_enabled": ws_enabled,
                    "web_search_force": ws_force,
                    "web_search_limit": ws_limit,
                    "web_search_max_keyword": ws_max_keyword,
                    "web_search_country": ws_country,
                    "web_search_region": ws_region,
                    "web_search_city": ws_city,
                    "web_search_api_key_preview": personastore.mask_api_key(effective_ws_key),
                    "web_search_api_key_is_set": bool(effective_ws_key),
                    "share_codex_memory": bool(cfg.share_codex_memory),
                    "hot_reloaded": True,
                },
            }
        )

    def _provider_allowed_bases(self, provider: str) -> set[str]:
        preset = self._PROVIDER_PRESETS.get(provider) or {}
        urls = preset.get("api_urls") if isinstance(preset, dict) else {}
        out = {str(v).rstrip("/") for v in (urls or {}).values() if v}
        base_url = str(preset.get("base_url") or "").strip() if isinstance(preset, dict) else ""
        if base_url:
            out.add(base_url.rstrip("/"))
        return out

    @staticmethod
    def _host_is_public(hostname: str | None) -> bool:
        if not hostname:
            return False
        clean = hostname.strip().strip("[]").lower()
        if clean in {"localhost"} or clean.endswith(".localhost") or clean.endswith(".local"):
            return False
        try:
            ip = ipaddress.ip_address(clean)
        except ValueError:
            return True
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    def _validated_llm_base(self, provider: str, api_base: str) -> str:
        provider = str(LLMConfig.normalize_provider_alias(provider) or "").strip()
        base = (api_base or self._PROVIDER_PRESETS.get(provider, {}).get("base_url") or "").strip().rstrip("/")
        if not base:
            raise ValueError("Base URL 未配置")
        try:
            parsed = urlsplit(base)
        except Exception as exc:
            raise ValueError("Base URL 格式无效") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 只允许 http(s) URL")

        allowed_bases = self._provider_allowed_bases(provider)
        if base in allowed_bases:
            return base
        if provider in _LLM_LOCAL_PROVIDER_ALLOWLIST:
            raise ValueError("本地 LLM provider 只允许使用内置本机 Base URL")
        if provider != "custom":
            raise ValueError("Base URL 必须匹配所选 provider 的内置白名单")
        if parsed.scheme != "https":
            raise ValueError("自定义 Base URL 必须使用 https")
        if not self._host_is_public(parsed.hostname):
            raise ValueError("自定义 Base URL 不允许指向本机、内网或链路本地地址")
        return base

    async def _probe_llm(
        self,
        *,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
        message_type: str,
    ) -> str | None:
        """Send a minimal ping. Return an error string or None on success."""
        if not api_key:
            return "API key 为空"
        if not model:
            return "未指定 model"
        try:
            base = self._validated_llm_base(provider, api_base)
        except ValueError as exc:
            return str(exc)
        try:
            import httpx
        except Exception:
            return "httpx 未安装，无法执行连接检测"

        if message_type == "anthropic":
            url = f"{base}/messages"
            # Triple-auth: real Anthropic uses `x-api-key`, MiMo's official
            # curl uses `api-key`, and third-party Anthropic-compat proxies
            # (including some MiMo wrappers) use `Authorization: Bearer`.
            # Sending all three keeps a single probe working across all of
            # them — each server picks up the header it understands.
            headers = {
                "x-api-key": api_key,
                "api-key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": model,
                "max_tokens": 4,
                "messages": [{"role": "user", "content": "ping"}],
            }
        else:
            url = f"{base}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
                "temperature": 0,
            }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return f"连接失败：{exc}"
        if resp.status_code >= 400:
            # best-effort extract provider message
            try:
                data = resp.json()
                err = data.get("error") or data
                msg = (err.get("message") if isinstance(err, dict) else None) or resp.text[:200]
            except Exception:
                msg = resp.text[:200] or f"HTTP {resp.status_code}"
            return f"Provider 返回 {resp.status_code}: {msg}"
        return None

    # ------------------------------------------------------------------
    # ESP32 wireless camera/mic device
    # ------------------------------------------------------------------

    async def api_esp32_status(self, request: Request) -> JSONResponse:
        """GET /api/device/status — discovery + health snapshot.

        Used by the frontend to decide:
          * whether to auto-open the WiFi setup wizard (no device found +
            device_esp32.enabled=true but never seen)
          * which banner to show (yellow = cold fallback to local, red =
            mid-session disconnect)
          * what to display in the Settings → 设备 tab
        """
        cfg = self.server.config.device_esp32
        status = self.server.esp32.get_status()
        mic_streaming = False
        wake_event_clients = 0
        wake_ready = False
        wake_model = ""
        wake_requested_model = ""
        wake_supported_models: list[str] = []
        led_status: dict[str, Any] = {}
        transfer_active = bool(
            self.server.esp32
            and hasattr(self.server.esp32, "is_transfer_active")
            and self.server.esp32.is_transfer_active()
        )
        if cfg.enabled and self.server.esp32 and not transfer_active:
            try:
                device_status_code, device_body, _ = await asyncio.wait_for(
                    self.server.esp32.proxy_get("/device/status"),
                    timeout=1.5,
                )
                if device_status_code == 200 and isinstance(device_body, dict):
                    mic_streaming = bool(device_body.get("mic_streaming") and device_body.get("wake_ready"))
                    wake_event_clients = int(device_body.get("wake_event_clients") or 0)
                    wake_ready = bool(device_body.get("wake_ready"))
                    wake_model = str(device_body.get("wake_model") or "")
                    wake_requested_model = str(device_body.get("wake_requested_model") or "")
                    led_status = {
                        "ready": bool(device_body.get("led_ready")),
                        "mode": device_body.get("led_mode"),
                        "mode_name": device_body.get("led_mode_name"),
                        "brightness": device_body.get("led_brightness"),
                        "last_command": device_body.get("led_last_command"),
                        "last_write_ms": device_body.get("led_last_write_ms"),
                        "driver": device_body.get("led_driver"),
                        "pixel_pin": device_body.get("led_pixel_pin"),
                        "pixel_count": device_body.get("led_pixel_count"),
                        "panel_count": device_body.get("led_panel_count"),
                        "output_ok": device_body.get("led_output_ok"),
                    }
                    raw_models = device_body.get("wake_supported_models") or []
                    if isinstance(raw_models, list):
                        wake_supported_models = [str(m) for m in raw_models if m]
            except Exception:
                pass
        status = self.server.esp32.get_status()
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "enabled": cfg.enabled,
                    "preferred_host": cfg.preferred_host,
                    "mic_enabled": cfg.mic_enabled,
                    "mic_streaming": mic_streaming,
                    "wake_ready": wake_ready,
                    "wake_model": wake_model,
                    "wake_requested_model": wake_requested_model,
                    "wake_supported_models": wake_supported_models,
                    "wake_event_clients": wake_event_clients,
                    "led": led_status,
                    "configured": status["configured"],
                    "online": status["online"],
                    "transfer_active": status.get("transfer_active", False),
                    "session_used": status["session_used"],
                    "owner_id": status.get("owner_id"),
                    "owner_label": status.get("owner_label"),
                    "blocked_devices_count": status.get("blocked_devices_count", 0),
                    "device": status["device"],
                    "all_devices": status["all_devices"],
                },
            }
        )

    async def api_esp32_restart_discovery(self, request: Request) -> JSONResponse:
        if not self.server.esp32 or not self.server.config.device_esp32.enabled:
            return JSONResponse({"ok": False, "error": "esp32_not_enabled"}, status_code=400)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        clear_devices = bool(payload.get("clear_devices", False)) if isinstance(payload, dict) else False
        await self.server.esp32.restart_discovery(clear_devices=clear_devices, reason="api")
        return JSONResponse({"ok": True, "result": self.server.esp32.get_status()})

    async def api_esp32_snapshot(self, request: Request) -> Any:
        """GET /api/device/snapshot — proxy a single JPEG frame from the device."""
        from starlette.responses import Response

        jpeg = await self.server.esp32.snapshot_jpeg()
        if jpeg is None:
            return JSONResponse({"ok": False, "error": "no_device_or_capture_failed"}, status_code=503)
        return Response(content=jpeg, media_type="image/jpeg")

    async def api_esp32_config(self, request: Request) -> JSONResponse:
        """GET returns the ESP32's live camera sensor config; POST forwards a patch."""
        if request.method == "GET":
            status, body, ctype = await self.server.esp32.proxy_get("/device/config")
            if isinstance(body, (bytes, bytearray)):
                try:
                    body = json.loads(bytes(body).decode("utf-8"))
                except Exception:
                    body = {"ok": False, "error": "non_json_response"}
            return JSONResponse(body if isinstance(body, dict) else {"ok": False, "raw": str(body)}, status_code=status)

        try:
            patch = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
        if not isinstance(patch, dict):
            return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
        if self.server.esp32 and hasattr(self.server.esp32, "with_owner_auth"):
            patch = self.server.esp32.with_owner_auth(patch, reason="config")
        status, body, _ = await self.server.esp32.proxy_post("/device/config", patch)
        return JSONResponse(body if isinstance(body, dict) else {"ok": False, "raw": str(body)}, status_code=status)

    async def api_esp32_led(self, request: Request) -> JSONResponse:
        """GET/POST the ESP32 LED UART bridge."""
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        if request.method == "GET":
            status, body, _ = await self.server.esp32.proxy_get("/device/led")
            if isinstance(body, (bytes, bytearray)):
                try:
                    body = json.loads(bytes(body).decode("utf-8"))
                except Exception:
                    body = {"ok": False, "error": "non_json_response"}
            return JSONResponse(self._with_expression_catalog(body), status_code=status)

        try:
            patch = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
        if not isinstance(patch, dict):
            return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)
        candidate = str(patch.get("preset_id") or patch.get("clip_id") or patch.get("expression") or "").strip().lower()
        preset_ids = {str(item.get("preset_id") or "") for item in list_expression_presets()}
        if patch.get("preset_id") or candidate in preset_ids:
            try:
                request_body = dict(patch)
                request_body["preset_id"] = str(patch.get("preset_id") or candidate)
                composition = resolve_expression(request_body)
            except (ExpressionLibraryError, ExpressionClipError, ValueError, TypeError) as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            status, body = await self._play_resolved_expression(composition)
            return JSONResponse(self._with_expression_catalog(body), status_code=status)
        if self.server.esp32 and hasattr(self.server.esp32, "with_owner_auth"):
            patch = self.server.esp32.with_owner_auth(patch, reason="led")
        status, body, _ = await self.server.esp32.proxy_post("/device/led", patch)
        return JSONResponse(self._with_expression_catalog(body), status_code=status)

    async def api_esp32_expression_clip_sync(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be object"}, status_code=400)

        clip_id = str(body.get("clip_id") or request.query_params.get("clip_id") or "").strip()
        if not clip_id:
            return JSONResponse({"ok": False, "error": "clip_id required"}, status_code=400)
        return await self._sync_eye_response(clip_id)

    async def _sync_eye_response(self, eye_id: str) -> JSONResponse:
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        try:
            clip_id = eye_storage_id(eye_id)
            manifest = load_expression_clip(clip_id)
            payload = load_expression_clip_lcd_payload(clip_id)
        except (ExpressionClipError, ExpressionLibraryError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            logger.exception("expression_clip.sync_prepare_failed", clip_id=clip_id)
            return JSONResponse({"ok": False, "error": f"sync prepare failed: {exc}"}, status_code=500)

        lcd_meta = manifest.get("lcd") or {}
        query = {
            "clip_id": manifest["clip_id"],
            "expression": manifest["expression"],
            "fps": manifest["fps"],
            "duration_ms": manifest["duration_ms"],
            "frame_count": manifest["frame_count"],
            "lcd_bytes": int(lcd_meta.get("bytes") or len(payload)),
            "lcd_sha256": str(lcd_meta.get("sha256") or ""),
            "led_effect": (manifest.get("led") or {}).get("effect") or manifest["expression"],
        }
        query = self.server.esp32.with_owner_auth(query, reason="expression_clip")
        status, last_body, _ = await self.server.esp32.proxy_post_bytes(
            "/device/expression-clips/upload",
            payload,
            params=query,
        )
        c6_confirmed = isinstance(last_body, dict) and last_body.get("c6_confirmed") is True
        if status >= 400 or (isinstance(last_body, dict) and last_body.get("ok") is False) or not c6_confirmed:
            device = self.server.esp32.get_status().get("device")
            update_expression_clip_sync(clip_id, status="sync_failed", device=device)
            return JSONResponse(
                {
                    "ok": False,
                    "error": "device sync failed" if status >= 400 else "C6 did not confirm clip sync",
                    "result": {
                        "transfer_mode": "bulk",
                        "sent_chunks": 1,
                        "device_status": status,
                        "device_body": last_body,
                    },
                },
                status_code=status if status >= 400 else 502,
            )

        device = self.server.esp32.get_status().get("device")
        manifest = update_expression_clip_sync(clip_id, status="synced", device=device)
        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "clip": manifest,
                    "transfer_mode": "bulk",
                    "sent_chunks": 1,
                    "device_status": status,
                    "device_body": last_body,
                },
            }
        )

    async def api_esp32_pair(self, request: Request) -> JSONResponse:
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        host = str(payload.get("host", "")).strip() if isinstance(payload, dict) else ""
        ok, body, status = await self.server.esp32.pair_device(host=host or None, reason="api")
        return JSONResponse(
            {
                "ok": ok,
                "result": {
                    "owner_id": self.server.esp32.owner_id,
                    "owner_label": self.server.esp32.owner_label,
                    "device": body,
                },
                "error": None if ok else (body.get("error") if isinstance(body, dict) else "pair_failed"),
            },
            status_code=200 if ok else (status if status >= 400 else 409),
        )

    async def api_esp32_unpair(self, request: Request) -> JSONResponse:
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        status, body = await self.server.esp32.unpair_device(reason="api")
        if 200 <= status < 300:
            try:
                from lampgo import personastore

                personastore.patch_overrides_toml({
                    "device_esp32": {"enabled": False, "mic_enabled": False, "preferred_host": ""},
                })
                self.server.config.device_esp32.enabled = False
                self.server.config.device_esp32.mic_enabled = False
                self.server.config.device_esp32.preferred_host = ""
                await self.server.esp32.shutdown()
                self.server.esp32.reset_session()
                self.server.esp32.update_config(self.server.config.device_esp32)
            except Exception:
                logger.exception("web.device_unpair_cleanup_failed")
        return JSONResponse(body if isinstance(body, dict) else {"ok": status < 400, "raw": str(body)}, status_code=status)

    async def api_esp32_claim(self, request: Request) -> JSONResponse:
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        ok = await self.server.esp32.claim_owner(reason="api")
        return JSONResponse(
            {
                "ok": ok,
                "result": {
                    "owner_id": self.server.esp32.owner_id,
                    "owner_label": self.server.esp32.owner_label,
                },
            },
            status_code=200 if ok else 409,
        )

    async def api_esp32_release(self, request: Request) -> JSONResponse:
        if not self.server.esp32:
            return JSONResponse({"ok": False, "error": "no_device"}, status_code=503)
        status, body = await self.server.esp32.release_owner(reason="api")
        return JSONResponse(body if isinstance(body, dict) else {"ok": False, "raw": str(body)}, status_code=status)

    async def ws_esp32_speaker(self, ws: WebSocket) -> None:
        """Proxy browser PCM16 frames to the ESP32 /ws/speaker endpoint."""
        if not self._is_websocket_authorized(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        logger.info("web.esp32_speaker_proxy_client_connected")
        if self._esp32_capture_active:
            logger.info("web.esp32_speaker_proxy_rejected_capture_active")
            await ws.close(code=1013)
            return
        self._esp32_speaker_clients.add(ws)
        base_url = self.server.esp32.get_active_base_url() if self.server.esp32 else None
        if not base_url:
            self._esp32_speaker_clients.discard(ws)
            await ws.close(code=1011)
            return
        claim_owner = getattr(self.server.esp32, "claim_owner", None)
        if callable(claim_owner):
            try:
                ok = await claim_owner(reason="speaker_proxy")
            except Exception:
                logger.debug("web.esp32_speaker_proxy_claim_failed", exc_info=True)
                ok = False
            if not ok:
                self._esp32_speaker_clients.discard(ws)
                await ws.close(code=1008)
                return

        # Speaker WS lives on the stream httpd (port 81). Batch browser-side
        # 20 ms PCM frames before forwarding so full-duplex calls do not starve
        # /ws/audio async sends with a high-frequency receive loop.
        import re
        stream_base = re.sub(r":(\d+)$", lambda m: f":{int(m.group(1)) + 1}", base_url)
        if stream_base == base_url:
            stream_base = base_url.rstrip("/") + ":81"
        owner_query = ""
        if self.server.esp32 and hasattr(self.server.esp32, "ws_owner_query"):
            owner_query = f"?{self.server.esp32.ws_owner_query()}"
        esp32_ws_url = stream_base.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + f"/ws/speaker{owner_query}"
        safe_esp32_ws_url = redact_ws_owner_token(esp32_ws_url)
        try:
            import websockets
        except ImportError:
            logger.warning("web.esp32_speaker_proxy_no_websockets")
            await ws.close(code=1011)
            return

        frames = 0
        bytes_sent = 0
        dropped_frames = 0
        pending_audio = bytearray()
        pending_started_at = 0.0
        batch_target_bytes = 1920  # 60 ms of PCM16LE @ 16 kHz mono.
        batch_max_delay_s = 0.06
        esp32_ws = None
        next_connect_at = 0.0

        async def close_esp32_ws() -> None:
            nonlocal esp32_ws
            if esp32_ws is None:
                return
            try:
                await esp32_ws.close()
            except Exception:
                pass
            esp32_ws = None

        async def ensure_esp32_ws():
            nonlocal esp32_ws, next_connect_at
            if esp32_ws is not None:
                return esp32_ws
            now = asyncio.get_running_loop().time()
            if now < next_connect_at:
                return None
            try:
                esp32_ws = await websockets.connect(
                    esp32_ws_url,
                    open_timeout=5.0,
                    close_timeout=2.0,
                    ping_interval=None,
                    max_size=None,
                )
            except Exception as exc:
                next_connect_at = now + 0.5
                logger.warning("web.esp32_speaker_proxy_connect_failed", url=safe_esp32_ws_url, error=str(exc))
                return None
            logger.info("web.esp32_speaker_proxy_connected", url=safe_esp32_ws_url)
            return esp32_ws

        async def flush_pending_audio(*, force: bool = False) -> bool:
            nonlocal frames, bytes_sent, dropped_frames, pending_audio, pending_started_at, next_connect_at
            if not pending_audio:
                return True
            now = asyncio.get_running_loop().time()
            if (
                not force
                and len(pending_audio) < batch_target_bytes
                and pending_started_at > 0.0
                and now - pending_started_at < batch_max_delay_s
            ):
                return True

            frame = bytes(pending_audio)
            pending_audio.clear()
            pending_started_at = 0.0

            sent = False
            for attempt in range(2):
                target = await ensure_esp32_ws()
                if target is None:
                    break
                try:
                    await target.send(frame)
                    sent = True
                    break
                except Exception as exc:
                    logger.warning(
                        "web.esp32_speaker_proxy_send_failed",
                        url=safe_esp32_ws_url,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                    await close_esp32_ws()
                    next_connect_at = 0.0
            if not sent:
                dropped_frames += 1
                if dropped_frames == 1 or dropped_frames % 50 == 0:
                    logger.warning(
                        "web.esp32_speaker_proxy_dropped",
                        dropped_frames=dropped_frames,
                        forwarded_frames=frames,
                        bytes=bytes_sent,
                    )
                return False

            frames += 1
            bytes_sent += len(frame)
            if frames == 1 or frames % 100 == 0:
                logger.info(
                    "web.esp32_speaker_proxy_forwarded",
                    frames=frames,
                    bytes=bytes_sent,
                )
            return True

        try:
            while True:
                frame = await ws.receive_bytes()
                if not frame:
                    continue
                if not pending_audio:
                    pending_started_at = asyncio.get_running_loop().time()
                pending_audio.extend(frame)
                await flush_pending_audio()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("web.esp32_speaker_proxy_failed", url=safe_esp32_ws_url)
            try:
                await ws.close(code=1011)
            except Exception:
                pass
        finally:
            try:
                await flush_pending_audio(force=True)
            except Exception:
                logger.debug("web.esp32_speaker_proxy_final_flush_failed", exc_info=True)
            await close_esp32_ws()
            self._esp32_speaker_clients.discard(ws)
            logger.info("web.esp32_speaker_proxy_closed", frames=frames, bytes=bytes_sent, dropped_frames=dropped_frames)

    async def api_esp32_reboot(self, request: Request) -> JSONResponse:
        status, body, _ = await self.server.esp32.proxy_post("/device/reboot", {})
        self.server.esp32.reset_session()
        return JSONResponse(body if isinstance(body, dict) else {"ok": False, "raw": str(body)}, status_code=status)

    async def api_esp32_forget_wifi(self, request: Request) -> JSONResponse:
        """POST /api/device/forget-wifi — clear NVS on ESP32 and reboot it into SoftAP.

        Also sets enabled/mic_enabled to false so the backend stops mDNS
        discovery until the next provisioning cycle re-enables them.
        """
        try:
            await self.server.esp32.unpair_device(reason="forget_wifi")
        except Exception:
            logger.debug("web.forget_wifi_unpair_failed", exc_info=True)
        payload = self.server.esp32.owner_auth_payload(reason="forget_wifi") if hasattr(self.server.esp32, "owner_auth_payload") else {}
        status, body, _ = await self.server.esp32.proxy_post("/device/forget-wifi", payload)
        try:
            await self.server.esp32.shutdown()
            self.server.esp32.reset_session()
            from lampgo import personastore
            personastore.patch_overrides_toml({
                "device_esp32": {"enabled": False, "mic_enabled": False, "preferred_host": ""},
            })
            self.server.config.device_esp32.enabled = False
            self.server.config.device_esp32.mic_enabled = False
            self.server.config.device_esp32.preferred_host = ""
            self.server.esp32.update_config(self.server.config.device_esp32)
        except Exception:
            logger.exception("web.forget_wifi_cleanup_failed")
        return JSONResponse(body if isinstance(body, dict) else {"ok": False, "raw": str(body)}, status_code=status)

    @staticmethod
    def _url_hostname(value: str) -> str:
        raw = value.strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = f"http://{raw}"
        try:
            return (urlsplit(raw).hostname or "").strip().strip("[]").lower()
        except Exception:
            return ""

    @staticmethod
    def _url_port(value: str) -> int | None:
        raw = value.strip()
        if not raw:
            return None
        if "://" not in raw:
            raw = f"http://{raw}"
        try:
            return urlsplit(raw).port
        except Exception:
            return None

    def _allowed_esp32_endpoints(self) -> set[tuple[str, int | None]]:
        out: set[tuple[str, int | None]] = {("192.168.4.1", None), ("192.168.4.1", 80)}
        active = self.server.esp32.get_active_base_url() if self.server.esp32 else ""
        preferred = str(getattr(self.server.config.device_esp32, "preferred_host", "") or "")
        for value in (active or "", preferred):
            host = self._url_hostname(value)
            if not host:
                continue
            port = self._url_port(value)
            out.add((host, port))
            if port is None:
                out.add((host, 80))
        return out

    def _validate_esp32_probe_target(self, base_url: str, path: str) -> tuple[str, str | None]:
        if path not in _ESP32_PROBE_PATHS:
            return "", f"probe path not allowed: {path}"
        try:
            parsed = urlsplit(base_url)
        except Exception:
            return "", "invalid base_url"
        if parsed.scheme != "http" or not parsed.netloc:
            return "", "base_url must be an http URL"
        if parsed.username or parsed.password:
            return "", "base_url credentials are not allowed"
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return "", "base_url must not include path, query, or fragment"
        host = (parsed.hostname or "").strip().strip("[]").lower()
        if not host:
            return "", "base_url host is required"
        port = parsed.port
        allowed = self._allowed_esp32_endpoints()
        if (host, port) in allowed or (host, None) in allowed:
            return base_url.rstrip("/"), None
        if host.endswith(".local") and re.fullmatch(r"lampgo-cam-[a-z0-9-]+\.local", host) and port in {None, 80}:
            return base_url.rstrip("/"), None
        return "", "base_url is not an allowed ESP32 endpoint"

    async def api_esp32_probe(self, request: Request) -> JSONResponse:
        """POST /api/device/probe — generic HTTP proxy to an ESP32 during setup.

        The WiFi wizard sits on the lampgo origin but needs to talk to a device
        that may be at one of two addresses:

          * ``http://192.168.4.1`` — the SoftAP in provisioning mode (user has
            joined Lampgo-Setup-XXXX on the same host running lampgo);
          * ``http://lampgo-cam-XXXX.local`` — the already-provisioned device
            that's just having new credentials pushed.

        Body: ``{"base_url": "http://...", "path": "/scan", "method": "GET"|"POST",
        "body": {...}|null}``. When ``base_url`` is empty we fall through to the
        discovered device (same behaviour as /api/device/config).
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

        base_url = str(payload.get("base_url", "")).strip().rstrip("/")
        path = str(payload.get("path", "/status"))
        if not path.startswith("/"):
            path = "/" + path
        method = str(payload.get("method", "GET")).upper()
        body = payload.get("body", None)
        if method not in {"GET", "POST"}:
            return JSONResponse({"ok": False, "error": f"unsupported method: {method}"}, status_code=400)
        if (
            method == "POST"
            and path == "/connect"
            and isinstance(body, dict)
            and self.server.esp32
            and hasattr(self.server.esp32, "pairing_payload")
        ):
            body = {**body, **self.server.esp32.pairing_payload()}

        if not base_url:
            active = self.server.esp32.get_active_base_url() if self.server.esp32 else None
            if not active:
                return JSONResponse(
                    {"ok": False, "error": "no_device", "hint": "SoftAP 直连地址为空且未发现已配网设备"},
                    status_code=503,
                )
            base_url = active.rstrip("/")

        base_url, target_error = self._validate_esp32_probe_target(base_url, path)
        if target_error:
            logger.warning(
                "web.esp32_probe_rejected",
                base_url=str(payload.get("base_url", ""))[:200],
                path=path,
                reason=target_error,
            )
            return JSONResponse({"ok": False, "error": target_error}, status_code=400)

        url = f"{base_url}{path}"
        try:
            async with httpx_module.AsyncClient(timeout=5.0, trust_env=False) as client:
                if method == "GET":
                    resp = await client.get(url)
                elif method == "POST":
                    resp = await client.post(url, json=body or {})
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return JSONResponse(
                {"ok": False, "error": f"probe_failed: {detail}", "url": url},
                status_code=502,
            )

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:512]}

        return JSONResponse(
            {
                "ok": resp.status_code < 400,
                "result": {
                    "status_code": resp.status_code,
                    "url": url,
                    "body": data,
                },
            }
        )

    async def api_esp32_capture_start(self, request: Request) -> JSONResponse:
        """POST /api/device/capture-audio/start — begin recording from ESP32 mic."""
        from lampgo.device.audio_stream import Esp32AudioSession

        if not self.server.esp32 or not self.server.config.device_esp32.enabled:
            return JSONResponse({"ok": False, "error": "esp32_not_enabled"}, status_code=400)

        if not hasattr(self, "_esp32_audio_session") or self._esp32_audio_session is None:
            self._esp32_audio_session = Esp32AudioSession(self.server.esp32)

        session: Esp32AudioSession = self._esp32_audio_session
        if session.is_recording:
            if self.server._wake_loop:
                self.server._wake_loop.pause_device_wake_listener(duration_s=90.0)
            return JSONResponse({"ok": True, "result": {"status": "already_recording"}})

        self._esp32_capture_active = True
        await self._stop_esp32_call_streams_for_capture()
        if self.server._wake_loop:
            self.server._wake_loop.pause_device_wake_listener(duration_s=90.0)
        await self._ensure_esp32_mic_stream_enabled()
        ok = await session.start()
        if not ok:
            self._esp32_capture_active = False
            if self.server._wake_loop:
                self.server._wake_loop.resume_device_wake_listener()
            return JSONResponse({"ok": False, "error": "esp32_offline"}, status_code=503)
        return JSONResponse({"ok": True, "result": {"status": "recording"}})

    async def api_esp32_capture_stop(self, request: Request) -> JSONResponse:
        """POST /api/device/capture-audio/stop — stop recording, return WAV."""
        import base64

        session = getattr(self, "_esp32_audio_session", None)
        if session is None or not session.is_recording:
            return JSONResponse({"ok": False, "error": "not_recording"}, status_code=400)

        wav_bytes = await session.stop()
        self._esp32_capture_active = False
        if self.server._wake_loop:
            self.server._wake_loop.resume_device_wake_listener()
        if wav_bytes is None:
            error = "capture_no_frames"
            if getattr(session, "last_pcm_bytes", 0) > 0:
                error = "capture_too_short"
            return JSONResponse({"ok": False, "error": error}, status_code=400)

        b64 = base64.b64encode(wav_bytes).decode("ascii")
        return JSONResponse({"ok": True, "result": {"audio_data": b64}})

    async def api_esp32_capture_cancel(self, request: Request) -> JSONResponse:
        """POST /api/device/capture-audio/cancel — discard recording."""
        session = getattr(self, "_esp32_audio_session", None)
        if session is not None:
            session.cancel()
        self._esp32_capture_active = False
        if self.server._wake_loop:
            self.server._wake_loop.resume_device_wake_listener()
        return JSONResponse({"ok": True, "result": {"status": "cancelled"}})

    async def api_persona_all(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        return JSONResponse({"ok": True, "result": {"files": personastore.read_all_personas()}})

    async def api_persona_single(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        name = request.path_params["name"].upper()
        if name not in personastore.PERSONA_FILES:
            return JSONResponse({"ok": False, "error": f"unknown persona: {name}"}, status_code=404)
        if request.method == "GET":
            return JSONResponse({"ok": True, "result": {"name": name, "content": personastore.read_persona(name)}})
        if not self._check_auth_token(request):
            return JSONResponse({"ok": False, "error": "invalid local token"}, status_code=403)
        body = await request.json()
        content = body.get("content", "")
        if not isinstance(content, str):
            return JSONResponse({"ok": False, "error": "content must be a string"}, status_code=400)
        personastore.write_persona(name, content)
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": {"name": name, "bytes": len(content.encode("utf-8"))}})

    async def api_persona_reset(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except Exception:
                body = {}
        which = body.get("which", "all")
        try:
            report = personastore.reset_persona(which)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": report})

    async def api_memory_core(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        if request.method == "GET":
            return JSONResponse({"ok": True, "result": {"content": personastore.read_memory_core()}})
        body = await request.json()
        content = body.get("content", "")
        if not isinstance(content, str):
            return JSONResponse({"ok": False, "error": "content must be a string"}, status_code=400)
        personastore.write_memory_core(content)
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": {"bytes": len(content.encode("utf-8"))}})

    async def api_memory_core_reset(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        report = personastore.reset_memory_core()
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": report})

    async def api_memory_daily(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        if request.method == "GET":
            date_param = request.query_params.get("date", "").strip()
            if not date_param:
                return JSONResponse(
                    {
                        "ok": True,
                        "result": {
                            "dates": personastore.list_memory_dates(),
                            "today": personastore.read_memory_daily("today"),
                        },
                    }
                )
            try:
                content = personastore.read_memory_daily(date_param)
            except ValueError as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            return JSONResponse(
                {
                    "ok": True,
                    "result": {
                        "date": date_param if date_param != "today" else None,
                        "content": content,
                    },
                }
            )
        if not self._check_auth_token(request):
            return JSONResponse({"ok": False, "error": "invalid local token"}, status_code=403)
        body = await request.json()
        bullets = body.get("bullets") or []
        if isinstance(bullets, str):
            bullets = [line for line in bullets.splitlines() if line.strip()]
        if not isinstance(bullets, list) or not bullets:
            return JSONResponse({"ok": False, "error": "bullets must be a non-empty list"}, status_code=400)
        date_param = str(body.get("date") or "").strip() or None
        try:
            path = personastore.append_memory_daily([str(b) for b in bullets], date_str=date_param)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        promote = bool(body.get("promote", False))
        if promote:
            core = personastore.read_memory_core()
            joined = "\n".join(f"- {str(b).lstrip('- ').strip()}" for b in bullets)
            personastore.write_memory_core(core.rstrip() + "\n\n" + joined + "\n")
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": {"path": str(path), "promoted": promote}})

    async def api_memory_summarize(self, request: Request) -> JSONResponse:
        body = await request.json()
        messages = body.get("messages") or []
        session_id = str(body.get("session_id") or "").strip()
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"ok": False, "error": "messages required"}, status_code=400)

        bullets = await self._summarize_messages(messages)
        if not bullets:
            return JSONResponse({"ok": True, "result": {"bullets": [], "skipped": "no-summary"}})

        from lampgo import personastore

        header = None
        if session_id:
            header = f"# {personastore._today_str()} 日记\n\n> session={session_id}\n"
        path = personastore.append_memory_daily(bullets, header=header)
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": {"bullets": bullets, "path": str(path)}})

    async def _summarize_messages(self, messages: list) -> list[str]:
        """Use fast_model to extract 1-3 bullet summaries."""
        cfg = self.server.config.llm
        if not cfg.api_key:
            return []
        transcript_parts: list[str] = []
        for m in messages[-40:]:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            text = str(m.get("content") or "").strip()
            if not text:
                continue
            text = text[:400]
            transcript_parts.append(f"{role}: {text}")
        transcript = "\n".join(transcript_parts).strip()
        if not transcript:
            return []

        system = (
            "你是 lampgo 的记忆助手。基于下面的对话片段，抽取 1-3 条值得长期记住的要点（事实/偏好/约定），"
            "每条不超过 40 字，用中文。只输出 bullet 行，每行以 `- ` 开头，不要其他内容。"
            "如果没有值得记忆的信息，直接输出一个字：无。"
        )
        base = (cfg.api_base or self._PROVIDER_PRESETS.get(cfg.provider, {}).get("base_url") or "").rstrip("/")
        if not base:
            return []
        try:
            import httpx
        except Exception:
            return []
        url = f"{base}/chat/completions"
        # 摘要任务的 token 预算读 llm.summary_max_tokens（UI 可改）。保留下限 1024
        # 兜底：推理模型（mimo-v2-omni / o-系列 / deepseek-r1）会把预算花在
        # reasoning_content 上，预算不够就 finish_reason=length、content 为空。
        summary_budget = max(1024, int(getattr(cfg, "summary_max_tokens", 0) or 0))
        payload: dict[str, Any] = {
            "model": cfg.fast_model or cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": transcript},
            ],
            "max_tokens": summary_budget,
            "temperature": 0.2,
        }
        # mimo-v2-omni 是推理模型；这个任务里它会花几百 token 思考却不吐内容。
        # chat_template_kwargs.enable_thinking=false 会跳过思考链，直接吐 bullet。
        # 对非 mimo provider 带上这个字段也无副作用（未知字段多半被忽略），但为
        # 保守起见仅在 mimo 下注入。
        if (cfg.provider or "").lower() == "mimo":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                logger.warning("memory.summarize.http_error", status=resp.status_code, body=resp.text[:200])
                return []
            data = resp.json()
            content = ""
            finish_reason = ""
            reasoning_len = 0
            if isinstance(data, dict):
                choices = data.get("choices") or []
                if choices:
                    choice = choices[0] or {}
                    finish_reason = str(choice.get("finish_reason") or "")
                    message = choice.get("message") or {}
                    content = str(message.get("content") or "")
                    reasoning_len = len(str(message.get("reasoning_content") or ""))
        except httpx.RequestError as exc:
            logger.warning(
                "memory.summarize.failed",
                error_type=type(exc).__name__,
                request_url=str(exc.request.url) if exc.request is not None else "",
            )
            return []
        except Exception as exc:
            logger.warning("memory.summarize.failed", error_type=type(exc).__name__)
            return []
        bullets: list[str] = []
        for line in (content or "").splitlines():
            norm = line.strip()
            if not norm:
                continue
            if norm in {"无", "None", "-"}:
                continue
            body_line = norm.lstrip("-•*· ").strip()
            if body_line:
                bullets.append(body_line)
        # Diagnostic: empty content with a non-trivial reasoning chain usually means
        # the model ran out of tokens while "thinking" — log loud enough so the user
        # can see why /memory/summarize keeps returning skipped=no-summary.
        if not bullets and not content.strip() and reasoning_len > 0:
            logger.warning(
                "memory.summarize.reasoning_only",
                model=payload["model"],
                finish_reason=finish_reason,
                reasoning_chars=reasoning_len,
                hint="reasoning model consumed all max_tokens before emitting content; try a non-reasoning fast_model",
            )
        elif not bullets and content.strip():
            logger.info(
                "memory.summarize.no_bullets",
                model=payload["model"],
                finish_reason=finish_reason,
                content_preview=content[:120],
            )
        return bullets[:3]

    async def api_debug_system_prompt(self, request: Request) -> JSONResponse:
        """Render the system prompt that would currently be sent to the LLM.

        Useful for verifying that SOUL / AGENTS / PROFILE / MEMORY files and
        today's daily notes are being injected. Returns both the individual
        blocks (for the Web UI to display them cleanly) and the fully-rendered
        prompt string.
        """
        try:
            from lampgo.perception.llm_client import (
                AGENT_SYSTEM_PROMPT_TEMPLATE,
                _build_agent_system_prompt,
            )
            from lampgo.persona.bundle import load_bundles
        except Exception as exc:  # pragma: no cover - defensive
            return JSONResponse({"ok": False, "error": f"import failed: {exc}"}, status_code=500)

        persona = None
        memory = None
        load_error = ""
        try:
            persona, memory = load_bundles(self.server.config)
        except Exception as exc:
            load_error = str(exc)

        # We deliberately don't include runtime joint positions here; they change
        # constantly and aren't what the user wants to verify with this endpoint.
        joint_state: dict[str, float] | None = None

        rendered_persona = ""
        rendered_memory = ""
        try:
            if persona is not None and hasattr(persona, "render"):
                rendered_persona = persona.render() or ""
            if memory is not None and hasattr(memory, "render"):
                rendered_memory = memory.render() or ""
        except Exception as exc:
            load_error = load_error or str(exc)

        recording_actions_prompt = build_recording_actions_prompt(Path(self.server.config.recordings_dir))
        full_prompt = _build_agent_system_prompt(
            joint_state,
            persona=persona,
            memory=memory,
            recording_actions_prompt=recording_actions_prompt,
        )

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "persona_block": rendered_persona,
                    "memory_block": rendered_memory,
                    "joint_state": joint_state or {},
                    "recording_actions_block": recording_actions_prompt,
                    "full_prompt": full_prompt,
                    "template_preview": AGENT_SYSTEM_PROMPT_TEMPLATE[:240] + "…",
                    "load_error": load_error,
                },
            }
        )

    # ---- persistent session cache ------------------------------------------

    async def api_sessions(self, request: Request) -> JSONResponse:
        """GET returns the whole snapshot; PUT overwrites it atomically.

        DELETE wipes every stored session (client passes {"confirm": true}).
        The frontend treats the server as the source of truth across
        browsers / process restarts, but keeps its own localStorage copy as
        an offline fallback.
        """
        from lampgo import sessionstore

        if request.method == "GET":
            snap = sessionstore.load_snapshot()
            return JSONResponse({"ok": True, "result": snap})

        if request.method == "PUT":
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
            try:
                stored = sessionstore.save_snapshot(body)
            except ValueError as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            return JSONResponse({"ok": True, "result": stored})

        # DELETE all
        try:
            body = await request.json() if (request.headers.get("content-type") or "").startswith("application/json") else {}
        except Exception:
            body = {}
        if not (isinstance(body, dict) and body.get("confirm") is True):
            return JSONResponse(
                {"ok": False, "error": 'DELETE requires {"confirm": true}'},
                status_code=400,
            )
        stored = sessionstore.clear_all()
        return JSONResponse({"ok": True, "result": stored})

    async def api_session_single(self, request: Request) -> JSONResponse:
        from lampgo import sessionstore

        session_id = request.path_params.get("session_id", "").strip()
        if not session_id:
            return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
        stored = sessionstore.delete_session(session_id)
        return JSONResponse({"ok": True, "result": stored})

    async def api_events_replay(self, request: Request) -> JSONResponse:
        """Return events with seq > `since` (default 0), up to `limit`.

        The browser calls this on boot to backfill the event log panel so that
        after a process restart or when opening the page in a fresh browser,
        the event history appears continuous.
        """
        from lampgo import eventstore

        try:
            since = int(request.query_params.get("since", "0") or "0")
        except Exception:
            since = 0
        try:
            limit = int(request.query_params.get("limit", "500") or "500")
        except Exception:
            limit = 500
        result = eventstore.get_store().replay(since=since, limit=limit)
        return JSONResponse({"ok": True, "result": result})

    def _check_auth_token(self, request: Request) -> bool:
        """Validate a privileged write request against the gateway token."""
        return self._is_request_authorized(request)

    def _websocket_origin_allowed(self, ws: WebSocket) -> bool:
        origin = ws.headers.get("origin", "").strip()
        if not origin:
            return True
        if origin == "null":
            return False
        try:
            parsed = urlsplit(origin)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        if self._is_loopback_host(parsed.hostname):
            return True
        expected_scheme = "https" if ws.url.scheme == "wss" else "http"
        return self._same_origin(origin, expected_scheme, ws.url.hostname, ws.url.port)

    def _websocket_token_candidates(self, ws: WebSocket) -> list[str]:
        return [
            self._bearer_token(ws.headers.get("authorization")),
            str(ws.headers.get("x-lampgo-token") or "").strip(),
            str(ws.cookies.get(_AUTH_COOKIE_NAME) or "").strip(),
            str(ws.query_params.get("token") or "").strip(),
        ]

    def _is_websocket_authorized(self, ws: WebSocket) -> bool:
        if self._is_test_client(ws):
            return True
        if not self._websocket_origin_allowed(ws):
            logger.warning(
                "web.ws_origin_rejected",
                path=ws.url.path,
                origin=ws.headers.get("origin", ""),
                client=getattr(getattr(ws, "client", None), "host", ""),
            )
            return False
        try:
            expected = self._expected_auth_token()
        except Exception:
            logger.exception("web.ws_auth_token_load_failed")
            return False
        if not expected:
            return False
        return any(token and hmac.compare_digest(token, expected) for token in self._websocket_token_candidates(ws))

    def _invalidate_persona_cache(self) -> None:
        try:
            from lampgo.persona.bundle import invalidate_bundles

            invalidate_bundles()
        except Exception:
            pass

    async def api_cancel(self, request: Request) -> JSONResponse:
        cancelled = await self._stop_all_ws_work(request_id=str(uuid.uuid4().hex[:12]))
        return JSONResponse({"ok": True, "result": {"status": "cancelled", "cancelled": cancelled}})

    async def api_estop(self, request: Request) -> JSONResponse:
        result = await self.server.handle_request({"cmd": "estop"})
        return JSONResponse(result)

    # ---- WebSocket endpoint ----

    async def ws_endpoint(self, ws: WebSocket) -> None:
        if not self._is_websocket_authorized(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        await self.bridge.add_client(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"ok": False, "error": "invalid json"})
                    continue
                msg_type = msg.get("type", "")
                if msg_type == "cancel_agent_task":
                    self._spawn_ws_background_task(self._handle_ws_message(ws, msg))
                    continue
                # Long-running messages must not block the receive loop; otherwise
                # urgent commands like `estop` cannot be processed until completion.
                run_async = msg_type in ("text", "audio", "recording_save") or (msg_type == "invoke" and bool(msg.get("wait", True)))
                if run_async:
                    if msg_type in ("text", "audio"):
                        prev_task = self._active_request_tasks.get(ws)
                        if prev_task is not None and not prev_task.done():
                            prev_task.cancel()
                            self.server.cancel_pending_tts()
                            await self.server.executor.cancel_current()
                            logger.info("web.preempt_active_request", msg_type=msg_type)
                    task = asyncio.create_task(self._handle_ws_message(ws, msg))
                    self._active_request_tasks[ws] = task
                    task.add_done_callback(lambda done, client=ws: self._clear_active_request_task(client, done))
                else:
                    await self._handle_ws_message(ws, msg)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("web.ws_error")
        finally:
            task = self._active_request_tasks.pop(ws, None)
            if task and not task.done():
                task.cancel()
            relay_task = self._esp32_relay_tasks.pop(ws, None)
            if relay_task and not relay_task.done():
                relay_task.cancel()
            await self.bridge.remove_client(ws)

    def _clear_active_request_task(self, ws: WebSocket, task: asyncio.Task) -> None:
        if self._active_request_tasks.get(ws) is task:
            self._active_request_tasks.pop(ws, None)

    def _spawn_ws_background_task(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._background_ws_tasks.add(task)
        task.add_done_callback(self._clear_ws_background_task)

    def _clear_ws_background_task(self, task: asyncio.Task[None]) -> None:
        self._background_ws_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("web.ws_background_task_failed", error=str(error))

    async def _stop_all_ws_work(self, *, request_id: str = "", ws: WebSocket | None = None) -> dict[str, int]:
        cancelled_ws = 0
        tasks = []
        if ws is not None:
            task = self._active_request_tasks.get(ws)
            if task is not None:
                tasks.append(task)
        else:
            tasks.extend(self._active_request_tasks.values())
        for task in set(tasks):
            if task is not None and not task.done():
                task.cancel()
                cancelled_ws += 1
        cancelled = await self.server.stop_all_interactions(request_id=request_id)
        cancelled["ws"] = cancelled_ws
        return cancelled

    async def _stop_esp32_call_streams_for_capture(self) -> dict[str, int]:
        """Close ESP32 call audio streams so push-to-talk capture owns /ws/audio."""
        relay_cancelled = 0
        for relay_ws, relay_task in list(self._esp32_relay_tasks.items()):
            self._esp32_relay_tasks.pop(relay_ws, None)
            if relay_task and not relay_task.done():
                relay_task.cancel()
                relay_cancelled += 1
        speaker_closed = 0
        for speaker_ws in list(self._esp32_speaker_clients):
            try:
                await speaker_ws.close(code=1013)
                speaker_closed += 1
            except Exception:
                pass
            finally:
                self._esp32_speaker_clients.discard(speaker_ws)
        if relay_cancelled or speaker_closed:
            logger.info(
                "web.esp32_capture_stopped_call_streams",
                relay_cancelled=relay_cancelled,
                speaker_closed=speaker_closed,
            )
        return {"relay_cancelled": relay_cancelled, "speaker_closed": speaker_closed}

    async def _handle_ws_message(self, ws: WebSocket, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type", "")
        request_id = msg.get("request_id", uuid.uuid4().hex[:12])

        if msg_type == "text":
            text = msg.get("input", "").strip()
            if not text:
                await ws.send_json({"ok": False, "error": "empty input", "request_id": request_id})
                return

            try:
                await self.server.events.publish(IntentRouting(text=text, request_id=request_id))
                history = _sanitize_chat_history(msg.get("history"))
                result = await self.server.handle_request(
                    {
                        "cmd": "text",
                        "input": text,
                        "request_id": request_id,
                        "history": history,
                        "enable_thinking": (
                            bool(msg.get("enable_thinking"))
                            if "enable_thinking" in msg
                            else bool(self.server.config.llm.enable_thinking)
                        ),
                    }
                )

                intent_type = result.get("result", {}).get("type", "unknown")
                skill_id = result.get("result", {}).get("skill_id")
                chat_resp = result.get("result", {}).get("response") or result.get("result", {}).get("chat_response")
                await self.server.events.publish(
                    IntentResolved(
                        intent_type=intent_type,
                        skill_id=skill_id,
                        chat_response=chat_resp,
                        source=result.get("result", {}).get("source", ""),
                        detail=result.get("result", {}).get("detail"),
                        matched_keyword=result.get("result", {}).get("matched_keyword"),
                        request_id=request_id,
                    )
                )
                if chat_resp:
                    await self.server.events.publish(ChatMessage(role="assistant", content=chat_resp, request_id=request_id))

                result["request_id"] = request_id
                await ws.send_json(result)
            except asyncio.CancelledError:
                await self._send_cancel_response(ws, request_id)

        elif msg_type == "audio":
            audio_data = msg.get("audio_data", "").strip()
            if not audio_data:
                await ws.send_json({"ok": False, "error": "empty audio_data", "request_id": request_id})
                return

            try:
                history = _sanitize_chat_history(msg.get("history"))
                result = await self.server.handle_request(
                    {
                        "cmd": "audio",
                        "audio_data": audio_data,
                        "request_id": request_id,
                        "history": history,
                        "enable_thinking": (
                            bool(msg.get("enable_thinking"))
                            if "enable_thinking" in msg
                            else bool(self.server.config.llm.enable_thinking)
                        ),
                    }
                )
                result["request_id"] = request_id
                await ws.send_json(result)
            except asyncio.CancelledError:
                await self._send_cancel_response(ws, request_id)

        elif msg_type == "invoke":
            result = await self.server.handle_request(
                {
                    "cmd": "invoke",
                    "skill_id": msg.get("skill_id", ""),
                    "params": msg.get("params", {}),
                    "wait": msg.get("wait", True),
                }
            )
            if not result.get("ok") and not result.get("error"):
                nested_error = ((result.get("result") or {}).get("error") or "").strip()
                if nested_error:
                    result["error"] = nested_error
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "status":
            result = self.server._handle_status()
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "list_cameras":
            result = self.server._handle_list_cameras()
            result["type"] = "list_cameras"
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "set_camera":
            port_val = str(msg.get("port", ""))
            result = self.server._handle_set_camera(port_val)
            if port_val == "esp32" and self.server.esp32 and not self.server.esp32._started:
                try:
                    await self.server.esp32.start()
                except Exception:
                    logger.exception("web.esp32_start_on_camera_switch")
            result["type"] = "set_camera"
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "list_mics":
            result = self.server._handle_list_mics()
            result["type"] = "list_mics"
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "set_mic":
            device_val = str(msg.get("device", ""))
            result = await self.server._handle_set_mic(device_val)
            result["type"] = "set_mic"
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "skills":
            result = self.server._handle_skills()
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "recordings":
            await ws.send_json(
                {
                    "ok": True,
                    "result": {"recordings": self._list_recordings()},
                    "request_id": request_id,
                }
            )

        elif msg_type == "recording_start":
            result = await self.server.start_recording_session(fps=int(msg.get("fps", 30) or 30))
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "recording_stop":
            result = await self.server.stop_recording_session()
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "recording_save":
            result = await self.server.save_recording_session(
                str(msg.get("name", "")),
                overwrite=bool(msg.get("overwrite", False)),
                description=str(msg.get("description", "") or msg.get("prompt", "")),
                expression=str(msg.get("expression", "")),
                expression_preset=str(msg.get("expression_preset", "") or msg.get("preset_id", "")),
            )
            result["request_id"] = request_id
            await ws.send_json(result)
            if result.get("ok"):
                await ws.send_json(
                    {
                        "ok": True,
                        "result": {"recordings": self._list_recordings()},
                        "request_id": request_id,
                    }
                )

        elif msg_type == "recording_discard":
            result = await self.server.discard_recording_session()
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "expressions":
            await ws.send_json(
                {
                    "ok": True,
                    "result": {
                        "expressions": self._list_expressions(),
                        "expression_catalog": self._list_expression_catalog(),
                        "eyes": list_eyes(),
                        "led_effects": list_led_effects(),
                        "presets": list_expression_presets(),
                    },
                    "request_id": request_id,
                }
            )

        elif msg_type == "agent_tasks":
            await ws.send_json(
                {
                    "ok": True,
                    "result": {"agent_tasks": self.server.agent.list_tasks()},
                    "request_id": request_id,
                }
            )

        elif msg_type == "cancel_agent_task":
            task_id = str(msg.get("task_id", "")).strip()
            if not task_id:
                await ws.send_json({"ok": False, "error": "task_id is required", "request_id": request_id})
                return
            ok = await self.server.agent.cancel_task(task_id)
            await ws.send_json(
                {
                    "ok": ok,
                    "result": {"agent_task": self.server.agent.get_task(task_id)},
                    "request_id": request_id,
                }
            )

        elif msg_type == "cancel":
            cancelled = await self._stop_all_ws_work(request_id=request_id)
            await ws.send_json({"ok": True, "result": {"status": "cancelled", "cancelled": cancelled}, "request_id": request_id})

        elif msg_type == "stop_loop":
            cancelled = await self._stop_all_ws_work(request_id=request_id)
            logger.info("web.stop_loop", request_id=request_id, cancelled=cancelled)
            if request_id:
                await self._send_cancel_response(ws, request_id)

        elif msg_type == "stop_tts":
            cancelled = self.server.cancel_pending_tts()
            await ws.send_json({"ok": True, "result": {"cancelled": cancelled}, "request_id": request_id})

        elif msg_type == "tts_playback_client":
            active = bool(msg.get("active", True))
            await self.bridge.claim_tts_client(ws, active=active)
            await ws.send_json({"ok": True, "result": {"active": active}, "request_id": request_id})

        elif msg_type == "start_conversation":
            result = await self.server.handle_request({"cmd": "start_conversation"})
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "stop_conversation":
            result = await self.server.handle_request({"cmd": "stop_conversation"})
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "start_esp32_relay":
            if self._esp32_capture_active:
                await ws.send_json(
                    {"ok": False, "error": "esp32_capture_active", "request_id": request_id}
                )
                return
            existing = self._esp32_relay_tasks.get(ws)
            if existing and not existing.done():
                await ws.send_json({"ok": True, "request_id": request_id})
            else:
                if self.server._wake_loop:
                    self.server._wake_loop.pause_device_wake_listener(duration_s=300.0)
                task = asyncio.create_task(self._relay_esp32_audio_to_browser(ws))
                self._esp32_relay_tasks[ws] = task
                task.add_done_callback(lambda _t, _ws=ws: self._esp32_relay_tasks.pop(_ws, None))
                await ws.send_json({"ok": True, "request_id": request_id})

        elif msg_type == "stop_esp32_relay":
            relay_task = self._esp32_relay_tasks.pop(ws, None)
            if relay_task and not relay_task.done():
                relay_task.cancel()
            if self.server._wake_loop:
                self.server._wake_loop.resume_device_wake_listener()
            await ws.send_json({"ok": True, "request_id": request_id})

        elif msg_type == "estop":
            result = await self.server.handle_request({"cmd": "estop"})
            result["request_id"] = request_id
            await ws.send_json(result)

        else:
            await ws.send_json({"ok": False, "error": f"unknown type: {msg_type}", "request_id": request_id})

    async def _relay_esp32_audio_to_browser(self, ws: WebSocket) -> None:
        """Forward ESP32 PCM16 frames directly to one browser WebSocket client."""
        from lampgo.device.audio_stream import build_ws_audio_url

        claim_owner = getattr(self.server.esp32, "claim_owner", None) if self.server.esp32 else None
        if callable(claim_owner):
            try:
                ok = await claim_owner(reason="audio_relay")
            except Exception:
                logger.debug("web.esp32_audio_relay_claim_failed", exc_info=True)
                ok = False
            if not ok:
                logger.warning("web.esp32_audio_relay_claim_denied")
                try:
                    await ws.send_json({"type": "event", "event": "Esp32AudioRelayError", "data": {"error": "ESP32 未配对到当前电脑或固件需更新"}})
                except Exception:
                    pass
                return

        url = None
        deadline = asyncio.get_running_loop().time() + 6.0
        while self.server.esp32 and asyncio.get_running_loop().time() < deadline:
            url = build_ws_audio_url(self.server.esp32)
            if url:
                break
            await asyncio.sleep(0.25)
        if not url:
            logger.warning("web.esp32_audio_relay_no_url")
            try:
                await ws.send_json({"type": "event", "event": "Esp32AudioRelayError", "data": {"error": "esp32 audio unavailable"}})
            except Exception:
                pass
            return

        try:
            import websockets
        except ImportError:
            logger.warning("web.esp32_audio_relay_no_websockets")
            try:
                await ws.send_json({"type": "event", "event": "Esp32AudioRelayError", "data": {"error": "websockets not installed"}})
            except Exception:
                pass
            return

        await self._ensure_esp32_mic_stream_enabled()

        frames = 0
        bytes_sent = 0
        idle_timeouts = 0
        idle_timeout_s = 5.0
        reconnect_delay_s = 1.0
        safe_url = redact_ws_owner_token(url)
        logger.info("web.esp32_audio_relay_connecting", url=safe_url)
        try:
            await ws.send_json({"type": "event", "event": "Esp32AudioRelayStatus", "data": {"state": "connecting", "url": safe_url}})
        except Exception:
            pass
        try:
            while True:
                if self._esp32_capture_active:
                    logger.info("web.esp32_audio_relay_exit_capture_active")
                    return
                try:
                    async with websockets.connect(
                        url,
                        open_timeout=5,
                        close_timeout=2,
                        ping_interval=None,
                        max_size=None,
                    ) as esp32_ws:
                        self.server.esp32.mark_active_healthy()
                        safe_url = redact_ws_owner_token(url)
                        logger.info("web.esp32_audio_relay_connected", url=safe_url)
                        try:
                            await ws.send_json({"type": "event", "event": "Esp32AudioRelayStatus", "data": {"state": "connected", "url": safe_url}})
                        except Exception:
                            pass
                        reconnect_delay_s = 1.0
                        while True:
                            try:
                                data = await asyncio.wait_for(esp32_ws.recv(), timeout=idle_timeout_s)
                            except asyncio.TimeoutError:
                                idle_timeouts += 1
                                if idle_timeouts == 1 or idle_timeouts % 6 == 0:
                                    logger.warning(
                                        "web.esp32_audio_relay_idle",
                                        url=redact_ws_owner_token(url),
                                        frames=frames,
                                        bytes=bytes_sent,
                                        idle_timeouts=idle_timeouts,
                                        timeout_s=idle_timeout_s,
                                    )
                                continue
                            if not isinstance(data, bytes) or not data:
                                continue
                            idle_timeouts = 0
                            await ws.send_bytes(data)
                            frames += 1
                            bytes_sent += len(data)
                            if frames == 1 or frames % 100 == 0:
                                logger.info("web.esp32_audio_relay_forwarded", frames=frames, bytes=bytes_sent)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "web.esp32_audio_relay_reconnect",
                        url=redact_ws_owner_token(url),
                        frames=frames,
                        bytes=bytes_sent,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        delay_s=reconnect_delay_s,
                    )
                    await asyncio.sleep(reconnect_delay_s)
                    reconnect_delay_s = min(reconnect_delay_s * 1.5, 10.0)
                    next_url = build_ws_audio_url(self.server.esp32)
                    if next_url:
                        url = next_url
                    else:
                        logger.warning("web.esp32_audio_relay_no_url_retry")
                        await asyncio.sleep(reconnect_delay_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "web.esp32_audio_relay_failed",
                url=redact_ws_owner_token(url),
                frames=frames,
                bytes=bytes_sent,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            try:
                await ws.send_json({"type": "event", "event": "Esp32AudioRelayError", "data": {"error": "esp32 audio relay failed"}})
            except Exception:
                pass
        finally:
            safe_url = redact_ws_owner_token(url)
            logger.info("web.esp32_audio_relay_closed", url=safe_url, frames=frames, bytes=bytes_sent)
            try:
                await ws.send_json(
                    {
                        "type": "event",
                        "event": "Esp32AudioRelayStatus",
                        "data": {"state": "closed", "url": safe_url, "frames": frames, "bytes": bytes_sent},
                    }
                )
            except Exception:
                pass
            if self.server._wake_loop:
                self.server._wake_loop.resume_device_wake_listener()

    async def _ensure_esp32_mic_stream_enabled(self) -> None:
        """Best-effort nudge so ESP32 starts publishing PCM before call relay."""
        if not self.server.esp32:
            return
        if not self.server.config.device_esp32.mic_enabled:
            self.server.config.device_esp32.mic_enabled = True
            self.server.esp32.update_config(self.server.config.device_esp32)
        try:
            payload = {"mic_enabled": True}
            if hasattr(self.server.esp32, "with_owner_auth"):
                payload = self.server.esp32.with_owner_auth(payload, reason="audio_relay_config")
            status, body, _ = await asyncio.wait_for(
                self.server.esp32.proxy_post("/device/config", payload),
                timeout=1.5,
            )
            if status >= 400:
                logger.warning("web.esp32_audio_relay_mic_enable_failed", status=status, body=str(body)[:200])
            else:
                logger.info("web.esp32_audio_relay_mic_enabled", status=status)
        except Exception as exc:
            logger.warning(
                "web.esp32_audio_relay_mic_enable_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _send_cancel_response(self, ws: WebSocket, request_id: str) -> None:
        """Publish cancellation events and send a response after stop_loop."""
        await self.server.events.publish(
            AgentFinished(
                request_id=request_id,
                stop_reason="user_cancelled",
                tool_call_count=0,
                response="已停止",
                detail="用户手动停止",
            )
        )
        try:
            await ws.send_json(
                {
                    "ok": True,
                    "result": {
                        "type": "agent",
                        "response": "已停止",
                        "source": "user",
                        "detail": "用户手动停止",
                        "stop_reason": "user_cancelled",
                    },
                    "request_id": request_id,
                }
            )
        except Exception:
            pass

    def _list_recordings(self) -> list[dict[str, str]]:
        recordings_dir = Path(self.server.config.recordings_dir)
        return list_recording_catalog(recordings_dir)

    def _list_expressions(self) -> list[str]:
        return [item["name"] for item in self._list_expression_catalog()]

    def _list_expression_catalog(self) -> list[dict[str, Any]]:
        clips_by_expression = {
            str(clip.get("expression") or "").strip().lower(): clip
            for clip in list_expression_clips()
            if str(clip.get("expression") or "").strip()
        }
        catalog: list[dict[str, Any]] = []
        for item in led_expression_catalog():
            enriched = dict(item)
            clip = clips_by_expression.get(str(item.get("name") or "").strip().lower())
            if clip:
                enriched["clip_available"] = True
                enriched["clip_id"] = clip.get("clip_id")
                enriched["clip_duration_ms"] = clip.get("duration_ms")
                enriched["clip_frame_count"] = clip.get("frame_count")
                enriched["clip_sync"] = clip.get("sync")
            else:
                enriched["clip_available"] = False
            catalog.append(enriched)
        return catalog

    def _with_expression_catalog(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            return {"ok": False, "raw": str(body)}
        merged = dict(body)
        merged.setdefault("expressions", self._list_expressions())
        merged.setdefault("expression_catalog", self._list_expression_catalog())
        return merged
