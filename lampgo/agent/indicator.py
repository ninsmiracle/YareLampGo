"""Translate external-agent task state into LampGo LED expressions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

import structlog

from lampgo.core.events import AgentTaskUpdated, EventBus

logger = structlog.get_logger(__name__)

ACTIVE_STATUSES = frozenset({"queued", "running", "cancelling"})
FAILED_STATUSES = frozenset({"failed", "cancelled", "interrupted"})
AGENT_LED_MODES = {
    "active": "focused",
    "completed": "check",
    "failed": "cross",
}


class AgentLedIndicator:
    """Observe task events without coupling the agent manager to hardware.

    LED I/O may use an ESP32 HTTP endpoint, so updates are serialized on a
    background worker instead of delaying Codex event streaming.
    """

    def __init__(self, events: EventBus, set_mode: Callable[[str], bool]) -> None:
        self._events = events
        self._set_mode = set_mode
        self._task_statuses: dict[str, str] = {}
        self._desired_mode: str | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        events.subscribe(AgentTaskUpdated, self.handle)

    async def handle(self, event: AgentTaskUpdated) -> None:
        if self._closed or not isinstance(event.task, dict):
            return
        task_id = str(event.task.get("task_id") or "").strip()
        status = str(event.task.get("status") or "").strip().lower()
        if not task_id or not status:
            return

        self._task_statuses[task_id] = status
        mode = self._resolve_mode(status)
        if mode is None or mode == self._desired_mode:
            return
        self._desired_mode = mode

        # Only the newest desired state matters if the device is slower than
        # Codex's event stream. An update already being sent is allowed to
        # finish, then the worker applies the latest queued mode.
        while not self._queue.empty():
            with suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._queue.task_done()
        self._queue.put_nowait(mode)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="agent-led-indicator")

    def _resolve_mode(self, latest_status: str) -> str | None:
        if any(status in ACTIVE_STATUSES for status in self._task_statuses.values()):
            return AGENT_LED_MODES["active"]
        if latest_status == "completed":
            return AGENT_LED_MODES["completed"]
        if latest_status in FAILED_STATUSES:
            return AGENT_LED_MODES["failed"]
        return None

    async def _run(self) -> None:
        try:
            while True:
                mode = await self._queue.get()
                try:
                    ok = await asyncio.to_thread(self._set_mode, mode)
                    if not ok:
                        logger.warning("agent_indicator.led_update_failed", mode=mode)
                except Exception:
                    logger.exception("agent_indicator.led_update_error", mode=mode)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    async def flush(self) -> None:
        """Wait until all queued LED updates have been attempted."""
        await self._queue.join()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._events.unsubscribe(AgentTaskUpdated, self.handle)
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        self._worker = None
