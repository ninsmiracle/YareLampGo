"""Web gateway — Starlette app serving REST API, WebSocket, and static UI."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

import httpx as httpx_module
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from lampgo.core.config import LLMConfig, WebConfig
from lampgo.core.events import AgentFinished, ChatMessage, IntentResolved, IntentRouting
from lampgo.core.led import LED_EXPRESSIONS
from lampgo.device.audio_stream import redact_ws_owner_token
from lampgo.perception.camera import CameraCapture
from lampgo.web.ws_bridge import WsBridge

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ASSETS_DIR = REPO_ROOT / "assets"


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
        self._esp32_relay_tasks: dict[WebSocket, asyncio.Task] = {}
        self._livekit_token_lock = asyncio.Lock()
        self._livekit_room_lock = asyncio.Lock()
        self._livekit_token_gate_until = 0.0
        self._livekit_token_gate_owner = ""
        self._livekit_active_rooms: dict[str, dict[str, Any]] = {}
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
            Route("/api/recordings/aliases", self.api_recording_aliases, methods=["GET", "POST"]),
            Route("/api/expressions", self.api_expressions),
            Route("/api/camera/snap", self.api_camera_snap),
            Route("/api/sensor/context", self.api_sensor_context),
            Route("/api/openclaw/ask", self.api_openclaw_ask, methods=["POST"]),
            Route("/api/openclaw/ask/reply", self.api_openclaw_ask_reply, methods=["POST"]),
            Route("/api/openclaw/callback", self.api_openclaw_callback, methods=["POST"]),
            Route("/api/openclaw/tasks", self.api_openclaw_tasks),
            Route("/api/openclaw/tasks/{task_id:str}/confirm", self.api_openclaw_confirm, methods=["POST"]),
            Route("/api/openclaw/health", self.api_openclaw_health),
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
            Route("/api/device/pair", self.api_esp32_pair, methods=["POST"]),
            Route("/api/device/unpair", self.api_esp32_unpair, methods=["POST"]),
            Route("/api/device/claim", self.api_esp32_claim, methods=["POST"]),
            Route("/api/device/release", self.api_esp32_release, methods=["POST"]),
            Route("/api/device/reboot", self.api_esp32_reboot, methods=["POST"]),
            Route("/api/device/forget-wifi", self.api_esp32_forget_wifi, methods=["POST"]),
            Route("/api/device/probe", self.api_esp32_probe, methods=["POST"]),
            Route("/api/device/capture-audio/start", self.api_esp32_capture_start, methods=["POST"]),
            Route("/api/device/capture-audio/stop", self.api_esp32_capture_stop, methods=["POST"]),
            Route("/api/device/capture-audio/cancel", self.api_esp32_capture_cancel, methods=["POST"]),
            WebSocketRoute("/api/device/speaker", self.ws_esp32_speaker),
            Route("/api/persona", self.api_persona_all, methods=["GET"]),
            Route("/api/persona/import-openclaw", self.api_persona_import, methods=["POST"]),
            Route("/api/persona/reset", self.api_persona_reset, methods=["POST"]),
            Route("/api/persona/{name:str}", self.api_persona_single, methods=["GET", "PUT"]),
            Route("/api/memory/core", self.api_memory_core, methods=["GET", "PUT"]),
            Route("/api/memory/core/reset", self.api_memory_core_reset, methods=["POST"]),
            Route("/api/memory/core/import", self.api_memory_core_import, methods=["POST"]),
            Route("/api/memory/daily", self.api_memory_daily, methods=["GET", "POST"]),
            Route("/api/memory/summarize", self.api_memory_summarize, methods=["POST"]),
            Route("/api/memory/openclaw", self.api_memory_openclaw, methods=["GET"]),
            Route("/api/debug/system-prompt", self.api_debug_system_prompt, methods=["GET"]),
            # ---- server-side persistent cache for chat sessions ----
            Route("/api/sessions", self.api_sessions, methods=["GET", "PUT", "DELETE"]),
            Route("/api/sessions/{session_id:str}", self.api_session_single, methods=["DELETE"]),
            # ---- event replay (so newly-connected browsers see historical events) ----
            Route("/api/events", self.api_events_replay, methods=["GET"]),
            WebSocketRoute("/ws", self.ws_endpoint),
            # ---- OpenAI-compatible LLM endpoint (for LiveKit Agent SDK) ----
            Route("/v1/chat/completions", self._llm_compat_handler, methods=["POST"]),
            # ---- pet model asset from repo-level CAD/export assets ----
            Route("/assets/pet/lampgo.glb", self.api_pet_model, methods=["GET"]),
            Route("/assets/pet/lampgoGLB.glb", self.api_pet_model, methods=["GET"]),
        ]
        if STATIC_DIR.is_dir():
            routes.append(Mount("/", app=StaticFiles(directory=str(STATIC_DIR), html=True)))

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

        app = Starlette(routes=routes, lifespan=lifespan)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
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

    # ---- OpenAI-compatible LLM endpoint ----

    async def _llm_compat_handler(self, request: Request) -> StreamingResponse:
        from lampgo.web.llm_compat import handle_chat_completions

        return await handle_chat_completions(request)

    async def api_pet_model(self, request: Request) -> FileResponse | JSONResponse:
        """Serve the exported pet GLB from repo-level assets if present."""
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
        / persistence / live-registration happens there so OpenClaw (going via
        IPC) and the Web UI (going via HTTP) exercise the exact same path.
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

    async def api_recordings_save(self, request: Request) -> JSONResponse:
        """POST /api/recordings/save — write a CSV recording + optional keyword alias.

        Body: { "name": "my_skill", "csv": "<csv content>", "alias": "触发词" (optional) }
        Saves to <recordings_dir>/user/<name>.csv (user-created recordings are isolated from
        built-in assets; the user/ subdirectory is gitignored).
        Updates aliases.json in recordings_dir root if alias provided.
        """
        body = await request.json()
        name = str(body.get("name", "")).strip()
        csv_content = body.get("csv", "")
        alias = str(body.get("alias", "")).strip()

        if not name or not re.match(r"^[\w\-]+$", name):
            return JSONResponse({"ok": False, "error": "name must be non-empty alphanumeric/dash/underscore"}, status_code=400)
        if not isinstance(csv_content, str) or not csv_content.strip():
            return JSONResponse({"ok": False, "error": "csv must be a non-empty string"}, status_code=400)

        recordings_dir = Path(self.server.config.recordings_dir)
        user_dir = recordings_dir / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        csv_path = user_dir / f"{name}.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        if alias:
            alias_path = recordings_dir / "aliases.json"
            try:
                existing: dict = json.loads(alias_path.read_text(encoding="utf-8")) if alias_path.exists() else {}
            except Exception:
                existing = {}
            existing[alias] = name
            alias_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return JSONResponse({"ok": True, "result": {"name": name, "path": str(csv_path), "alias": alias or None}})

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
        return JSONResponse({"ok": True, "result": {"expressions": self._list_expressions()}})

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

    async def api_openclaw_ask(self, request: Request) -> JSONResponse:
        body = await request.json()
        question = str(body.get("question", "")).strip()
        if not question:
            return JSONResponse({"ok": False, "error": "question_required"}, status_code=400)
        options = body.get("options") or []
        if not isinstance(options, list):
            options = []
        options = [str(item) for item in options if str(item).strip()]
        request_id = str(body.get("request_id", "")).strip()
        timeout_s = float(body.get("timeout_s", 120.0))
        result = await self.server.openclaw_ask_user(
            question=question,
            options=options,
            request_id=request_id,
            timeout_s=timeout_s,
        )
        return JSONResponse({"ok": True, "result": result})

    async def api_openclaw_ask_reply(self, request: Request) -> JSONResponse:
        body = await request.json()
        ask_id = str(body.get("ask_id", "")).strip()
        reply = str(body.get("reply", "")).strip()
        request_id = str(body.get("request_id", "")).strip()
        if not ask_id or not reply:
            return JSONResponse({"ok": False, "error": "ask_id_and_reply_required"}, status_code=400)
        ok = await self.server.openclaw_reply_user(ask_id=ask_id, reply=reply, request_id=request_id)
        return JSONResponse({"ok": ok, "result": {"accepted": ok}})

    async def api_openclaw_callback(self, request: Request) -> JSONResponse:
        body = await request.json()
        # Free-form status payload from the OpenClaw plugin.
        status = body.get("status")
        detail = body.get("detail")
        request_id = str(body.get("request_id", "")).strip()
        if status:
            await self.server.events.publish(
                ChatMessage(role="assistant", content=f"[OpenClaw] {status}: {detail or ''}".strip(), request_id=request_id)
            )
        return JSONResponse({"ok": True, "result": {"accepted": True}})

    async def api_openclaw_tasks(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "result": {"openclaw_tasks": self.server.openclaw.list_tasks()}})

    async def api_openclaw_health(self, request: Request) -> JSONResponse:
        from lampgo.bridge.openclaw_installer import detect_openclaw_integration

        status = detect_openclaw_integration()
        tasks = self.server.openclaw.list_tasks()
        running_statuses = {
            "queued",
            "planning",
            "executing",
            "executing_after_promotion",
            "executing_with_existing_tools",
            "awaiting_confirmation",
        }
        running = sum(1 for t in tasks if str(t.get("status", "")) in running_statuses)

        if not status.binary.ok or status.overall == "missing":
            connection = "not_installed"
        elif status.overall == "degraded":
            connection = "degraded"
        elif running > 0:
            connection = "running"
        else:
            connection = "idle"

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "connection": connection,
                    "integration": status.as_dict(),
                    "gateway_running": bool(status.gateway.ok),
                    "running_tasks": running,
                    "total_tasks": len(tasks),
                },
            }
        )

    async def api_livekit_token(self, request: Request) -> JSONResponse:
        """Proxy token requests to the managed Xiaomi LiveKit Agent SDK."""
        try:
            body = await request.json()
        except Exception:
            body = {}

        room_name = str(body.get("room_name") or self.server.config.voice.livekit_room or "lampgo")
        user_identity = str(body.get("user_identity") or f"lampgo-web-{uuid.uuid4().hex[:8]}")
        voice_agent = str(body.get("voice_agent") or "lampgo-jarvis")
        if not body.get("client_call_id"):
            logger.info("web.livekit_token_legacy_client_rejected", user_identity=user_identity)
            return JSONResponse({"ok": False, "error": "please refresh the web UI before starting a call"}, status_code=409)
        client_call_id = str(body.get("client_call_id"))
        reason = str(body.get("reason") or "")
        logger.info(
            "web.livekit_token_requested",
            room=room_name,
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
                    room_name=room_name,
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
        room_name: str,
        user_identity: str,
        voice_agent: str,
        client_call_id: str,
        reason: str,
    ) -> JSONResponse:
        from lampgo.voice.agent_sdk import AGENT_SDK_PORT

        await self._close_existing_livekit_rooms(
            keep_room=room_name,
            reason=f"new_{reason or 'call'}",
            client_call_id=client_call_id,
        )
        agent_sdk = getattr(self.server, "_agent_sdk", None)
        wait_ready = getattr(agent_sdk, "wait_ready", None)
        if callable(wait_ready):
            ready = await wait_ready(timeout_s=10.0)
            if not ready:
                logger.info(
                    "web.livekit_token_agent_not_ready",
                    room=room_name,
                    user_identity=user_identity,
                    voice_agent=voice_agent,
                    client_call_id=client_call_id,
                )
                async with self._livekit_token_lock:
                    if self._livekit_token_gate_owner == client_call_id:
                        self._livekit_token_gate_until = 0.0
                        self._livekit_token_gate_owner = ""
                return JSONResponse(
                    {"ok": False, "error": "voice agent is still starting"},
                    status_code=503,
                )
        async with httpx_module.AsyncClient(timeout=10) as client:
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
        configured = str(self.server.config.voice.livekit_room or "lampgo").strip() or "lampgo"
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

        vc = self.server.config.voice
        if vc.livekit_url and vc.livekit_api_key and vc.livekit_api_secret:
            try:
                from livekit import api

                lkapi = api.LiveKitAPI(
                    vc.livekit_url,
                    vc.livekit_api_key,
                    vc.livekit_api_secret,
                )
                try:
                    listed = await lkapi.room.list_rooms(api.ListRoomsRequest())
                    for room in listed.rooms:
                        name = str(getattr(room, "name", "") or "")
                        if name != keep_room and self._is_managed_livekit_room(name):
                            rooms.add(name)
                finally:
                    await lkapi.aclose()
            except Exception as exc:
                logger.warning(
                    "web.livekit_room_list_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    keep_room=keep_room,
                )

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
        vc = self.server.config.voice
        targets = sorted(
            {
                str(room or "").strip()
                for room in rooms
                if self._is_managed_livekit_room(str(room or "").strip())
            }
        )
        if not targets:
            return []
        if not (vc.livekit_url and vc.livekit_api_key and vc.livekit_api_secret):
            for room in targets:
                self._livekit_active_rooms.pop(room, None)
            logger.warning("web.livekit_room_delete_skipped_no_config", rooms=targets, reason=reason)
            return []

        from livekit import api

        closed: list[str] = []
        lkapi = api.LiveKitAPI(
            vc.livekit_url,
            vc.livekit_api_key,
            vc.livekit_api_secret,
        )
        try:
            for room in targets:
                try:
                    await lkapi.room.delete_room(api.DeleteRoomRequest(room=room))
                    closed.append(room)
                    logger.info(
                        "web.livekit_room_deleted",
                        room=room,
                        reason=reason,
                        client_call_id=client_call_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "web.livekit_room_delete_failed",
                        room=room,
                        reason=reason,
                        client_call_id=client_call_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                finally:
                    self._livekit_active_rooms.pop(room, None)
        finally:
            await lkapi.aclose()
        return closed

    async def api_openclaw_confirm(self, request: Request) -> JSONResponse:
        body = await request.json()
        task_id = request.path_params["task_id"]
        proposal_id = str(body.get("proposal_id", "")).strip()
        decision = str(body.get("decision", "")).strip()
        if not proposal_id or decision not in {"approve", "reject"}:
            return JSONResponse({"ok": False, "error": "proposal_id and decision are required"}, status_code=400)
        try:
            task = await self.server.openclaw.confirm_promotion(task_id, proposal_id, decision)
        except KeyError:
            return JSONResponse({"ok": False, "error": "task or proposal not found"}, status_code=404)
        return JSONResponse({"ok": True, "result": {"openclaw_task": task}})

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
            "label": "MiMo（小米）",
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

    # Fields that require a daemon restart to take effect (can't be applied
    # to a live motion loop without reconnecting hardware).
    #
    # Deliberately NOT listed here:
    #   - camera.port      → hot-swapped via the set_camera WS command in
    #                        `server._handle_set_camera`; the Web UI also
    #                        rebroadcasts this on save.
    #   - voice.mic_device → hot-swapped via set_mic / WakeLoop.set_mic_device.
    _COLD_RESTART_FIELDS: frozenset[str] = frozenset(
        {
            "device.motor_port",
            "device.led_port",
            "device.lamp_id",
            "device.use_degrees",
            "led.port",
            "led.baud_rate",
            "web.host",
            "web.port",
            "socket_path",
        }
    )

    _VOICE_HOT_RELOAD_FIELDS: frozenset[str] = frozenset(
        {
            "voice.wake_word",
            "voice.livekit_url",
            "voice.livekit_api_key",
            "voice.livekit_api_secret",
            "voice.livekit_room",
            "voice.volcengine_app_id",
            "voice.volcengine_access_token",
        }
    )

    # Map web UI section → (allowed LampgoConfig paths, restart-only fields).
    # Each path uses the same dotted notation as the provenance map.
    _SECTION_FIELDS: dict[str, tuple[str, ...]] = {
        "device": (
            "device.motor_port",
            "device.led_port",
            "device.lamp_id",
            "device.use_degrees",
            "led.port",
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
            "voice.livekit_url",
            "voice.livekit_api_key",
            "voice.livekit_api_secret",
            "voice.livekit_room",
            "voice.silence_timeout_s",
            "voice.volcengine_app_id",
            "voice.volcengine_access_token",
            "voice.livekit_tts_voice",
        ),
        "motion": (
            "motion.tick_rate_hz",
            "motion.default_max_velocity",
            "motion.default_style",
            "motion.default_playback_mode",
            "motion.breathing_enabled",
            "motion.breathing_amplitude",
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
            out[dotted] = {
                "value": value,
                "source": provenance.get(dotted, "default"),
            }
        return out

    def _list_env_overrides(self, provenance: dict[str, str]) -> list[str]:
        return sorted(k for k, v in provenance.items() if v == "env")

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
            setattr(obj, tail, coerced)

    async def api_config_device(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "device")

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
            share_memory = bool(getattr(self.server.config, "share_openclaw_memory", True))
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
                        "share_openclaw_memory": share_memory,
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
        # Quick path: caller only toggles share_openclaw_memory.
        if "share_openclaw_memory" in body and not any(
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

            share = bool(body.get("share_openclaw_memory"))
            personastore.patch_overrides_toml({"share_openclaw_memory": share})
            self.server.config.share_openclaw_memory = share
            self._invalidate_persona_cache()
            return JSONResponse({"ok": True, "result": {"share_openclaw_memory": share}})

        validate = bool(body.get("validate", True))
        dry_run = bool(body.get("dry_run", False))
        # PATCH-like semantics: when the client omits a key from the body,
        # keep the current value.  This matters because the "MiMo 联网搜索"
        # card saves with a subset of fields and MUST NOT clobber the main
        # LLM's api_base / message_type / fast_model.  The short-circuit at
        # the top of this handler already covers the share-memory-only POST;
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
        share_memory = body.get("share_openclaw_memory")

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

        # ------------------------------------------------------------------
        # MiMo web search sub-service fields.
        #
        # These travel alongside the main LLM settings because (a) the UI
        # surfaces them in the same card and (b) ``web_search`` falls back
        # to reusing the main ``api_key`` when ``provider == "mimo"`` (see
        # ``_resolve_web_search_api_key``).  But they are logically
        # **independent** of the main LLM path — web search always talks
        # MiMo OpenAI-compat no matter what ``provider`` / ``message_type``
        # the primary LLM is set to.  Keep that invariant when touching
        # this block.
        # ------------------------------------------------------------------
        def _coerce_bounded_int(raw: Any, fallback: int, *, lo: int, hi: int) -> int:
            if raw is None or raw == "":
                return int(fallback)
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return int(fallback)
            return max(lo, min(hi, v))

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

        ws_enabled = _opt_bool(body.get("web_search_enabled"), current_llm.web_search_enabled)
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
            patch["share_openclaw_memory"] = bool(share_memory)
        personastore.patch_overrides_toml(patch)

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
        cfg.llm.web_search_enabled = ws_enabled
        cfg.llm.web_search_force = ws_force
        cfg.llm.web_search_limit = ws_limit
        cfg.llm.web_search_max_keyword = ws_max_keyword
        cfg.llm.web_search_country = ws_country
        cfg.llm.web_search_region = ws_region
        cfg.llm.web_search_city = ws_city
        cfg.llm.web_search_api_key = effective_ws_key
        if share_memory is not None:
            cfg.share_openclaw_memory = bool(share_memory)

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
                    "share_openclaw_memory": bool(cfg.share_openclaw_memory),
                    "hot_reloaded": True,
                },
            }
        )

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
        base = api_base.rstrip("/") or self._PROVIDER_PRESETS.get(provider, {}).get("base_url") or ""
        if not base:
            return "Base URL 未配置"
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
        if cfg.mic_enabled and self.server.esp32:
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
                    "configured": status["configured"],
                    "online": status["online"],
                    "session_used": status["session_used"],
                    "owner_id": status.get("owner_id"),
                    "owner_label": status.get("owner_label"),
                    "blocked_devices_count": status.get("blocked_devices_count", 0),
                    "device": status["device"],
                    "all_devices": status["all_devices"],
                },
            }
        )

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
        await ws.accept()
        logger.info("web.esp32_speaker_proxy_client_connected")
        base_url = self.server.esp32.get_active_base_url() if self.server.esp32 else None
        if not base_url:
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
                await ws.close(code=1008)
                return

        # Speaker WS lives on the stream httpd (port 81) to avoid contention
        # with the mic push task on the main httpd (port 80).
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

        try:
            while True:
                frame = await ws.receive_bytes()
                if not frame:
                    continue
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
                    continue
                frames += 1
                bytes_sent += len(frame)
                if frames == 1 or frames % 100 == 0:
                    logger.info(
                        "web.esp32_speaker_proxy_forwarded",
                        frames=frames,
                        bytes=bytes_sent,
                    )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("web.esp32_speaker_proxy_failed", url=safe_esp32_ws_url)
            try:
                await ws.close(code=1011)
            except Exception:
                pass
        finally:
            await close_esp32_ws()
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

        url = f"{base_url}{path}"
        try:
            async with httpx_module.AsyncClient(timeout=5.0) as client:
                if method == "GET":
                    resp = await client.get(url)
                elif method == "POST":
                    resp = await client.post(url, json=body or {})
                else:
                    return JSONResponse({"ok": False, "error": f"unsupported method: {method}"}, status_code=400)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"probe_failed: {exc}", "url": url},
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

        if self.server._wake_loop:
            self.server._wake_loop.pause_device_wake_listener(duration_s=90.0)
        await self._ensure_esp32_mic_stream_enabled()
        ok = await session.start()
        if not ok:
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
        if not self._check_plugin_token(request):
            return JSONResponse({"ok": False, "error": "invalid plugin token"}, status_code=403)
        body = await request.json()
        content = body.get("content", "")
        if not isinstance(content, str):
            return JSONResponse({"ok": False, "error": "content must be a string"}, status_code=400)
        personastore.write_persona(name, content)
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": {"name": name, "bytes": len(content.encode("utf-8"))}})

    async def api_persona_import(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        which = body.get("which", "safe")
        report = personastore.import_persona_from_openclaw(which)
        self._invalidate_persona_cache()
        return JSONResponse({"ok": True, "result": report})

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

    async def api_memory_core_import(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        report = personastore.import_memory_core_from_openclaw()
        if not report.get("source"):
            return JSONResponse(
                {"ok": False, "error": "OpenClaw 里没有找到 MEMORY.md（~/.openclaw/MEMORY.md 或 ~/.openclaw/workspace/MEMORY.md）"},
                status_code=404,
            )
        if not report.get("imported"):
            return JSONResponse({"ok": False, "error": "导入失败，请查看日志"}, status_code=500)
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
            return JSONResponse(
                {
                    "ok": True,
                    "result": {
                        "date": date_param if date_param != "today" else None,
                        "content": personastore.read_memory_daily(date_param),
                    },
                }
            )
        if not self._check_plugin_token(request):
            return JSONResponse({"ok": False, "error": "invalid plugin token"}, status_code=403)
        body = await request.json()
        bullets = body.get("bullets") or []
        if isinstance(bullets, str):
            bullets = [line for line in bullets.splitlines() if line.strip()]
        if not isinstance(bullets, list) or not bullets:
            return JSONResponse({"ok": False, "error": "bullets must be a non-empty list"}, status_code=400)
        date_param = str(body.get("date") or "").strip() or None
        path = personastore.append_memory_daily([str(b) for b in bullets], date_str=date_param)
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

    async def api_memory_openclaw(self, request: Request) -> JSONResponse:
        from lampgo import personastore

        return JSONResponse({"ok": True, "result": personastore.openclaw_memory_preview()})

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

        full_prompt = _build_agent_system_prompt(joint_state, persona=persona, memory=memory)

        return JSONResponse(
            {
                "ok": True,
                "result": {
                    "persona_block": rendered_persona,
                    "memory_block": rendered_memory,
                    "joint_state": joint_state or {},
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

    def _check_plugin_token(self, request: Request) -> bool:
        """If the request carries an X-Lampgo-Plugin-Token header, validate it.

        Absence of the header is treated as browser / same-origin UI access and
        is allowed (the gateway binds to localhost by default). When a token is
        present, it must match the server's stored value exactly.
        """
        supplied = request.headers.get("x-lampgo-plugin-token")
        if not supplied:
            return True
        try:
            from lampgo import personastore

            expected = personastore.get_plugin_token()
        except Exception:
            return False
        if not expected:
            return False
        return supplied.strip() == expected

    def _invalidate_persona_cache(self) -> None:
        try:
            from lampgo.persona.bundle import invalidate_bundles

            invalidate_bundles()
        except Exception:
            pass

    async def api_cancel(self, request: Request) -> JSONResponse:
        await self.server.executor.cancel_current()
        return JSONResponse({"ok": True, "result": {"status": "cancelled"}})

    async def api_estop(self, request: Request) -> JSONResponse:
        result = await self.server.handle_request({"cmd": "estop"})
        return JSONResponse(result)

    # ---- WebSocket endpoint ----

    async def ws_endpoint(self, ws: WebSocket) -> None:
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
                # Long-running messages must not block the receive loop; otherwise
                # urgent commands like `estop` cannot be processed until completion.
                run_async = msg_type in ("text", "audio", "recording_save") or (msg_type == "invoke" and bool(msg.get("wait", True)))
                if run_async:
                    task = asyncio.create_task(self._handle_ws_message(ws, msg))
                    self._active_request_tasks[ws] = task
                    task.add_done_callback(lambda _t: self._active_request_tasks.pop(ws, None))
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
                        "enable_thinking": bool(msg.get("enable_thinking")),
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
                        "enable_thinking": bool(msg.get("enable_thinking")),
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
                    "result": {"expressions": self._list_expressions()},
                    "request_id": request_id,
                }
            )

        elif msg_type == "openclaw_tasks":
            await ws.send_json(
                {
                    "ok": True,
                    "result": {"openclaw_tasks": self.server.openclaw.list_tasks()},
                    "request_id": request_id,
                }
            )

        elif msg_type == "confirm_promotion":
            proposal_id = str(msg.get("proposal_id", "")).strip()
            decision = str(msg.get("decision", "")).strip()
            task_id = str(msg.get("task_id", "")).strip()
            if not task_id or not proposal_id or decision not in {"approve", "reject"}:
                await ws.send_json({"ok": False, "error": "task_id, proposal_id and decision are required", "request_id": request_id})
                return
            try:
                task = await self.server.openclaw.confirm_promotion(task_id, proposal_id, decision)
            except KeyError:
                await ws.send_json({"ok": False, "error": "task or proposal not found", "request_id": request_id})
                return
            await ws.send_json({"ok": True, "result": {"openclaw_task": task}, "request_id": request_id})

        elif msg_type == "cancel":
            await self.server.executor.cancel_current()
            await ws.send_json({"ok": True, "result": {"status": "cancelled"}, "request_id": request_id})

        elif msg_type == "stop_loop":
            task = self._active_request_tasks.get(ws)
            cancelled = 0
            if task and not task.done():
                task.cancel()
                cancelled = 1
            self.server.cancel_pending_tts()
            await self.server.executor.cancel_current()
            logger.info("web.stop_loop", request_id=request_id, cancelled=cancelled)

        elif msg_type == "stop_tts":
            cancelled = self.server.cancel_pending_tts()
            await ws.send_json({"ok": True, "result": {"cancelled": cancelled}, "request_id": request_id})

        elif msg_type == "start_conversation":
            result = await self.server.handle_request({"cmd": "start_conversation"})
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "stop_conversation":
            result = await self.server.handle_request({"cmd": "stop_conversation"})
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "start_esp32_relay":
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

    def _list_recordings(self) -> list[str]:
        recordings_dir = Path(self.server.config.recordings_dir)
        if not recordings_dir.exists():
            return []
        # Built-in recordings in root; user-created in user/ subdir.
        # User recordings shadow built-ins of the same name.
        names: dict[str, str] = {p.stem: "builtin" for p in recordings_dir.glob("*.csv")}
        user_dir = recordings_dir / "user"
        if user_dir.is_dir():
            for p in user_dir.glob("*.csv"):
                names[p.stem] = "user"
        return sorted(names)

    def _list_expressions(self) -> list[str]:
        return sorted(LED_EXPRESSIONS.keys())
