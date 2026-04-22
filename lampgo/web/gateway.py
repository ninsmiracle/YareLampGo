"""Web gateway — Starlette app serving REST API, WebSocket, and static UI."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from lampgo.core.config import LLMConfig, WebConfig
from lampgo.core.led import LED_EXPRESSIONS
from lampgo.core.events import AgentFinished, ChatMessage, IntentResolved, IntentRouting
from lampgo.perception.camera import CameraCapture
from lampgo.web.ws_bridge import WsBridge

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


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


class WebGateway:
    """HTTP + WebSocket gateway that wraps a LampgoServer."""

    def __init__(self, server: LampgoServer, config: WebConfig | None = None) -> None:
        self.server = server
        self.config = config or WebConfig()
        self.bridge = WsBridge(server.events)
        self._status_task: asyncio.Task | None = None
        self._active_request_tasks: dict[WebSocket, asyncio.Task] = {}
        self.app = self._build_app()

    def _build_app(self) -> Starlette:
        routes = [
            Route("/api/text", self.api_text, methods=["POST"]),
            Route("/api/invoke", self.api_invoke, methods=["POST"]),
            Route("/api/status", self.api_status),
            Route("/api/skills", self.api_skills),
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
            Route("/api/cancel", self.api_cancel, methods=["POST"]),
            Route("/api/estop", self.api_estop, methods=["POST"]),
            # ---- user-editable config / persona / memory ----
            Route("/api/config", self.api_config_all, methods=["GET"]),
            Route("/api/config/device", self.api_config_device, methods=["POST"]),
            Route("/api/config/voice", self.api_config_voice, methods=["POST"]),
            Route("/api/config/motion", self.api_config_motion, methods=["POST"]),
            Route("/api/config/safety", self.api_config_safety, methods=["POST"]),
            Route("/api/config/web", self.api_config_web, methods=["POST"]),
            Route("/api/config/detect", self.api_config_detect, methods=["POST"]),
            Route("/api/config/restart", self.api_config_restart, methods=["POST"]),
            Route("/api/config/llm", self.api_config_llm, methods=["GET", "POST"]),
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
        ]
        if STATIC_DIR.is_dir():
            routes.append(Mount("/", app=StaticFiles(directory=str(STATIC_DIR), html=True)))

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
            self._status_task = asyncio.create_task(self._status_loop())
            yield
            if self._status_task:
                self._status_task.cancel()
                try:
                    await self._status_task
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

    # ---- REST endpoints ----

    async def api_text(self, request: Request) -> JSONResponse:
        body = await request.json()
        text = body.get("input", "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty input"}, status_code=400)

        request_id = body.get("request_id", uuid.uuid4().hex[:12])
        await self.server.events.publish(IntentRouting(text=text, request_id=request_id))

        result = await self.server.handle_request({"cmd": "text", "input": text, "request_id": request_id})

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
        camera = CameraCapture(self.server.config.camera)
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

    async def api_sensor_context(self, request: Request) -> JSONResponse:
        camera = CameraCapture(self.server.config.camera)
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
                        "enabled": bool(self.server.config.voice_enabled),
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
            await self.server.events.publish(ChatMessage(role="assistant", content=f"[OpenClaw] {status}: {detail or ''}".strip(), request_id=request_id))
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

    _PROVIDER_PRESETS = {
        "mimo": {
            "label": "MiMo（小米）",
            "base_url": "https://api.xiaomimimo.com/v1",
            "default_model": "mimo-v2-omni",
            # mimo-v2-pro：非推理的对话模型，更适合"记忆总结/意图分类"这类
            # 轻量短输出任务。mimo-v2-omni 是推理模型，会把预算全花在 reasoning
            # 上导致正式 content 空返（参见 _summarize_messages 里的注释）。
            "default_fast_model": "mimo-v2-pro",
            "message_type": "openai",
        },
        "openrouter": {
            "label": "OpenRouter",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "anthropic/claude-3.5-sonnet",
            "default_fast_model": "anthropic/claude-3.5-haiku",
            "message_type": "openai",
        },
        "anthropic": {
            "label": "Anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "default_model": "claude-sonnet-4-20250514",
            "default_fast_model": "claude-haiku-4-20250514",
            "message_type": "anthropic",
        },
        "openai": {
            "label": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o-mini",
            "default_fast_model": "gpt-4o-mini",
            "message_type": "openai",
        },
        "deepseek": {
            "label": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
            "default_fast_model": "deepseek-chat",
            "message_type": "openai",
        },
        "google": {
            "label": "Google Gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "default_model": "gemini-2.5-flash",
            "default_fast_model": "gemini-2.5-flash",
            "message_type": "openai",
        },
        "ollama": {
            "label": "Ollama（本地）",
            "base_url": "http://127.0.0.1:11434/v1",
            "default_model": "qwen2.5:7b-instruct",
            "default_fast_model": "qwen2.5:7b-instruct",
            "message_type": "openai",
        },
        "custom": {
            "label": "自定义",
            "base_url": "",
            "default_model": "",
            "default_fast_model": "",
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
    #   - voice.mic_device → only read when a VoiceLoop is (re)constructed.
    #                        Web UI users capture mic in the browser, so the
    #                        server-side device never opens. CLI users pick
    #                        it up on the next `--voice` invocation.
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
            "voice.tts_provider",
            "voice.tts_voice",
            "voice.mic_device",
            "camera.port",
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
        "web": (
            "web.port",
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

        sections = {
            name: self._dump_section(fields, provenance)
            for name, fields in self._SECTION_FIELDS.items()
        }

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
        return await self._save_section(request, "voice")

    async def api_config_motion(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "motion")

    async def api_config_safety(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "safety")

    async def api_config_web(self, request: Request) -> JSONResponse:
        return await self._save_section(request, "web")

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
                        "请在启动 lampgo 的终端按 Ctrl+C 退出当前进程，再重跑 "
                        "`uv run lampgo run --web`。硬件相关字段需要重新打开串口才会生效。"
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
            message_type = (
                (overrides.get("llm") or {}).get("message_type")
                if isinstance(overrides, dict)
                else None
            ) or "openai"
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
                        "share_openclaw_memory": share_memory,
                        "provider_presets": self._PROVIDER_PRESETS,
                    },
                }
            )

        body = await request.json()
        # Quick path: caller only toggles share_openclaw_memory.
        if (
            "share_openclaw_memory" in body
            and not any(
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
                )
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
        provider = str(body.get("provider") or "").strip() or self.server.config.llm.provider
        provider = str(LLMConfig.normalize_provider_alias(provider) or "")
        api_base = str(body.get("api_base") or "").strip()
        api_key_raw = body.get("api_key")
        api_key = str(api_key_raw).strip() if api_key_raw is not None else ""
        model = str(body.get("model") or "").strip() or self.server.config.llm.model
        fast_model = str(body.get("fast_model") or "").strip() or model
        message_type = str(body.get("message_type") or "").strip() or "openai"
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

        current_llm = self.server.config.llm
        max_tokens_val = _coerce_positive_int(body.get("max_tokens"), current_llm.max_tokens)
        summary_max_tokens_val = _coerce_positive_int(
            body.get("summary_max_tokens"), current_llm.summary_max_tokens
        )
        context_window_val = _coerce_positive_int(
            body.get("context_window"), current_llm.context_window
        )
        temperature_val = _coerce_temperature(body.get("temperature"), current_llm.temperature)

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
                    "api_key_preview": personastore.mask_api_key(effective_key),
                    "api_key_is_set": bool(effective_key),
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
            headers = {
                "x-api-key": api_key,
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
        except Exception:
            logger.exception("memory.summarize.failed")
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
                return JSONResponse(
                    {"ok": False, "error": "invalid JSON body"}, status_code=400
                )
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
                {"ok": False, "error": "DELETE requires {\"confirm\": true}"},
                status_code=400,
            )
        stored = sessionstore.clear_all()
        return JSONResponse({"ok": True, "result": stored})

    async def api_session_single(self, request: Request) -> JSONResponse:
        from lampgo import sessionstore

        session_id = request.path_params.get("session_id", "").strip()
        if not session_id:
            return JSONResponse(
                {"ok": False, "error": "session_id required"}, status_code=400
            )
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
                run_async = msg_type in ("text", "audio", "recording_save") or (
                    msg_type == "invoke" and bool(msg.get("wait", True))
                )
                if run_async:
                    task = asyncio.create_task(self._handle_ws_message(ws, msg))
                    self._active_request_tasks[ws] = task
                    task.add_done_callback(lambda _t: self._active_request_tasks.pop(ws, None))
                else:
                    await self._handle_ws_message(ws, msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("web.ws_error")
        finally:
            task = self._active_request_tasks.pop(ws, None)
            if task and not task.done():
                task.cancel()
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
                result = await self.server.handle_request({"cmd": "text", "input": text, "request_id": request_id})

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
                result = await self.server.handle_request({"cmd": "audio", "audio_data": audio_data, "request_id": request_id})
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
            result = self.server._handle_set_camera(str(msg.get("port", "")))
            result["type"] = "set_camera"
            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "list_mics":
            result = self.server._handle_list_mics()
            result["type"] = "list_mics"
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

        elif msg_type == "estop":
            result = await self.server.handle_request({"cmd": "estop"})
            result["request_id"] = request_id
            await ws.send_json(result)

        else:
            await ws.send_json({"ok": False, "error": f"unknown type: {msg_type}", "request_id": request_id})

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
            await ws.send_json({
                "ok": True,
                "result": {
                    "type": "agent",
                    "response": "已停止",
                    "source": "user",
                    "detail": "用户手动停止",
                    "stop_reason": "user_cancelled",
                },
                "request_id": request_id,
            })
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
