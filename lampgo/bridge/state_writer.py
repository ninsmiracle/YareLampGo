"""Write a minimal lampgo state marker into the OpenClaw workspace.

We keep this intentionally tiny (human-readable, <20 lines) to avoid polluting
OpenClaw's prompt context. Detailed state and perception data should be fetched
on-demand via tools.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MinimalState:
    status: str
    is_busy: bool
    running_skill: str | None
    estopped: bool
    camera_connected: bool
    mic_active: bool


class StateWriter:
    def __init__(self, *, workspace_dir: Path | None = None, interval_s: float = 1.0) -> None:
        self._workspace_dir = workspace_dir or (Path.home() / ".openclaw" / "workspace")
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._last_payload: str | None = None
        self._running = False

    def start(self, *, get_state) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(get_state))

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self, get_state) -> None:
        while self._running:
            try:
                state: MinimalState = get_state()
                payload = self._format(state)
                if payload != self._last_payload:
                    self._write(payload)
                    self._last_payload = payload
            except Exception:
                logger.exception("state_writer.loop_error")
            await asyncio.sleep(self._interval_s)

    def _write(self, payload: str) -> None:
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        path = self._workspace_dir / "lampgo-state.md"
        path.write_text(payload, encoding="utf-8")

    @staticmethod
    def _format(state: MinimalState) -> str:
        updated = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        status = "busy" if state.is_busy else "idle"
        running = state.running_skill or "-"
        return "\n".join(
            [
                f"# lampgo device (updated: {updated})",
                f"- status: {status}",
                f"- running_skill: {running}",
                f"- estopped: {str(state.estopped).lower()}",
                f"- camera: {'connected' if state.camera_connected else 'disabled'}",
                f"- mic: {'active' if state.mic_active else 'inactive'}",
                "",
            ]
        )

