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

from lampgo.core.config import WebConfig
from lampgo.core.led import LED_EXPRESSIONS
from lampgo.core.events import AgentFinished, ChatMessage, IntentResolved, IntentRouting
from lampgo.perception.camera import CameraCapture
from lampgo.web.ws_bridge import WsBridge

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


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
            Route("/api/cancel", self.api_cancel, methods=["POST"]),
            Route("/api/estop", self.api_estop, methods=["POST"]),
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
