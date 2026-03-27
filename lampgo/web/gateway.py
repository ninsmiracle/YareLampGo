"""Web gateway — Starlette app serving REST API, WebSocket, and static UI."""

from __future__ import annotations

import asyncio
import json
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
from lampgo.core.events import ChatMessage, IntentResolved, IntentRouting
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
        self.app = self._build_app()

    def _build_app(self) -> Starlette:
        routes = [
            Route("/api/text", self.api_text, methods=["POST"]),
            Route("/api/invoke", self.api_invoke, methods=["POST"]),
            Route("/api/status", self.api_status),
            Route("/api/skills", self.api_skills),
            Route("/api/recordings", self.api_recordings),
            Route("/api/expressions", self.api_expressions),
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

        result = await self.server.handle_request({"cmd": "text", "input": text})

        intent_type = result.get("result", {}).get("type", "unknown")
        skill_id = result.get("result", {}).get("skill_id")
        chat_response = result.get("result", {}).get("response") or result.get("result", {}).get("chat_response")
        await self.server.events.publish(
            IntentResolved(
                intent_type=intent_type,
                skill_id=skill_id,
                chat_response=chat_response,
                request_id=request_id,
            )
        )
        if chat_response:
            await self.server.events.publish(
                ChatMessage(role="assistant", content=chat_response, request_id=request_id)
            )

        result["request_id"] = request_id
        return JSONResponse(result)

    async def api_invoke(self, request: Request) -> JSONResponse:
        body = await request.json()
        result = await self.server.handle_request({
            "cmd": "invoke",
            "skill_id": body.get("skill_id", ""),
            "params": body.get("params", {}),
            "wait": body.get("wait", True),
        })
        return JSONResponse(result)

    async def api_status(self, request: Request) -> JSONResponse:
        result = self.server._handle_status()
        return JSONResponse(result)

    async def api_skills(self, request: Request) -> JSONResponse:
        result = self.server._handle_skills()
        return JSONResponse(result)

    async def api_recordings(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "result": {"recordings": self._list_recordings()}})

    async def api_expressions(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "result": {"expressions": self._list_expressions()}})

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
                await self._handle_ws_message(ws, msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("web.ws_error")
        finally:
            await self.bridge.remove_client(ws)

    async def _handle_ws_message(self, ws: WebSocket, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type", "")
        request_id = msg.get("request_id", uuid.uuid4().hex[:12])

        if msg_type == "text":
            text = msg.get("input", "").strip()
            if not text:
                await ws.send_json({"ok": False, "error": "empty input", "request_id": request_id})
                return

            await self.server.events.publish(IntentRouting(text=text, request_id=request_id))
            result = await self.server.handle_request({"cmd": "text", "input": text})

            intent_type = result.get("result", {}).get("type", "unknown")
            skill_id = result.get("result", {}).get("skill_id")
            chat_resp = result.get("result", {}).get("response") or result.get("result", {}).get("chat_response")
            await self.server.events.publish(
                IntentResolved(
                    intent_type=intent_type,
                    skill_id=skill_id,
                    chat_response=chat_resp,
                    request_id=request_id,
                )
            )
            if chat_resp:
                await self.server.events.publish(
                    ChatMessage(role="assistant", content=chat_resp, request_id=request_id)
                )

            result["request_id"] = request_id
            await ws.send_json(result)

        elif msg_type == "invoke":
            result = await self.server.handle_request({
                "cmd": "invoke",
                "skill_id": msg.get("skill_id", ""),
                "params": msg.get("params", {}),
                "wait": msg.get("wait", True),
            })
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

        elif msg_type == "expressions":
            await ws.send_json(
                {
                    "ok": True,
                    "result": {"expressions": self._list_expressions()},
                    "request_id": request_id,
                }
            )

        elif msg_type == "cancel":
            await self.server.executor.cancel_current()
            await ws.send_json({"ok": True, "result": {"status": "cancelled"}, "request_id": request_id})

        elif msg_type == "estop":
            result = await self.server.handle_request({"cmd": "estop"})
            result["request_id"] = request_id
            await ws.send_json(result)

        else:
            await ws.send_json({"ok": False, "error": f"unknown type: {msg_type}", "request_id": request_id})

    def _list_recordings(self) -> list[str]:
        recordings_dir = Path(self.server.config.recordings_dir)
        if not recordings_dir.exists():
            return []
        return sorted(path.stem for path in recordings_dir.glob("*.csv"))

    def _list_expressions(self) -> list[str]:
        return sorted(LED_EXPRESSIONS.keys())
