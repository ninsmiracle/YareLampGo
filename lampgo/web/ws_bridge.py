"""Bridge EventBus events to WebSocket clients."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from starlette.websockets import WebSocket, WebSocketState

from lampgo.core.events import (
    AgentFinished,
    ChatMessage,
    EStopActivated,
    EStopReset,
    Event,
    EventBus,
    IntentProgress,
    IntentResolved,
    IntentRouting,
    OpenClawAskRequested,
    OpenClawAskResolved,
    OpenClawPromotionDecision,
    OpenClawPromotionRequested,
    OpenClawTaskUpdated,
    SafetyTriggered,
    SkillCancelled,
    SkillFinished,
    SkillProgress,
    SkillStarted,
    ToolCallFinished,
    ToolCallPlanned,
    TtsAudio,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

ALL_EVENT_TYPES: list[type[Event]] = [
    SkillStarted,
    SkillFinished,
    SkillCancelled,
    SafetyTriggered,
    EStopActivated,
    EStopReset,
    IntentRouting,
    IntentProgress,
    IntentResolved,
    OpenClawAskRequested,
    OpenClawAskResolved,
    OpenClawTaskUpdated,
    OpenClawPromotionRequested,
    OpenClawPromotionDecision,
    ToolCallPlanned,
    ToolCallFinished,
    AgentFinished,
    SkillProgress,
    ChatMessage,
    TtsAudio,
]


class WsBridge:
    """Subscribes to EventBus and broadcasts JSON events to all WebSocket clients."""

    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        for evt_type in ALL_EVENT_TYPES:
            events.subscribe(evt_type, self._on_event)

    async def add_client(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        logger.info("ws_bridge.client_connected", total=len(self._clients))

    async def remove_client(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info("ws_bridge.client_disconnected", total=len(self._clients))

    async def _on_event(self, event: Event) -> None:
        msg = self._serialize(event)
        await self.broadcast(msg)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    async def broadcast_status(self, status: dict[str, Any]) -> None:
        """Push a periodic status snapshot to all clients."""
        await self.broadcast({"type": "status", "data": status, "ts": time.time()})

    @staticmethod
    def _serialize(event: Event) -> dict[str, Any]:
        return {
            "type": "event",
            "event": type(event).__name__,
            "data": asdict(event),
            "ts": time.time(),
        }

    @property
    def client_count(self) -> int:
        return len(self._clients)
