"""Persistent append-only event log with replay support.

Every event that goes through `WsBridge` is also appended to
`~/.lampgo/events.log` as one JSONL record per line:

    {"seq": 1234, "ts": 17160..., "type": "event", "event": "ChatMessage", "data": {...}}

The next connecting client (after a process restart, or in a different
browser entirely) can ask `GET /api/events?since=<last_seq>` to replay
missed events into its UI event log, giving the illusion of a continuous
session across restarts and browsers.

Rotation: when the current log exceeds `MAX_BYTES`, it is renamed to
`events.log.1` (with older rotated files shifted to `.2`, `.3`, ...
up to `KEEP_ROTATIONS`), and a fresh file is started. The `seq` counter
persists across rotations and restarts.

Thread/async safety: a module-level asyncio lock serializes append+rotate.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import structlog

from lampgo.personastore import lampgo_home

logger = structlog.get_logger(__name__)

MAX_BYTES = 10 * 1024 * 1024  # 10 MB per segment before rotation
KEEP_ROTATIONS = 3
DEFAULT_REPLAY_LIMIT = 500
MAX_REPLAY_LIMIT = 5000


def events_path() -> Path:
    return lampgo_home() / "events.log"


def _rotated_path(n: int) -> Path:
    return lampgo_home() / f"events.log.{n}"


class EventStore:
    """Append-only event journal. One instance per process."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._seq = self._recover_last_seq()

    # ---- recovery ----

    def _recover_last_seq(self) -> int:
        """Scan the current log tail (and rotated files if empty) for the max seq."""
        candidates: list[Path] = [events_path()]
        for i in range(1, KEEP_ROTATIONS + 1):
            candidates.append(_rotated_path(i))
        for p in candidates:
            if not p.exists() or p.stat().st_size == 0:
                continue
            # Read last line cheaply. Files are typically < 10 MB so we can
            # read them whole; if larger we fall back to streaming.
            try:
                if p.stat().st_size <= 512 * 1024:
                    lines = p.read_text(encoding="utf-8").rstrip().splitlines()
                else:
                    lines = []
                    with p.open("rb") as fh:
                        fh.seek(0, os.SEEK_END)
                        size = fh.tell()
                        chunk = min(size, 64 * 1024)
                        fh.seek(size - chunk)
                        tail = fh.read(chunk).decode("utf-8", errors="replace")
                        lines = tail.rstrip().splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        seq = int(obj.get("seq", 0) or 0)
                        if seq > 0:
                            return seq
                    except Exception:
                        continue
            except Exception:
                logger.exception("eventstore.recover_failed", path=str(p))
        return 0

    # ---- append ----

    async def append(self, payload: dict[str, Any]) -> int:
        """Append `payload` (already JSON-serializable), tag with a new seq.

        Returns the seq assigned to this entry. Rotates the file when needed.
        """
        async with self._lock:
            self._seq += 1
            record = dict(payload)
            record["seq"] = self._seq
            record.setdefault("ts", time.time())
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            path = events_path()
            try:
                if path.exists() and path.stat().st_size >= MAX_BYTES:
                    self._rotate()
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.write("\n")
                    fh.flush()
            except Exception:
                logger.exception("eventstore.append_failed", path=str(path))
            return self._seq

    def _rotate(self) -> None:
        """Shift events.log → .1, .1 → .2, ..., drop oldest."""
        oldest = _rotated_path(KEEP_ROTATIONS)
        if oldest.exists():
            try:
                oldest.unlink()
            except Exception:
                logger.exception("eventstore.rotate_unlink_failed", path=str(oldest))
        for i in range(KEEP_ROTATIONS - 1, 0, -1):
            src = _rotated_path(i)
            dst = _rotated_path(i + 1)
            if src.exists():
                try:
                    src.rename(dst)
                except Exception:
                    logger.exception("eventstore.rotate_failed", src=str(src), dst=str(dst))
        current = events_path()
        if current.exists():
            try:
                current.rename(_rotated_path(1))
            except Exception:
                logger.exception("eventstore.rotate_current_failed", src=str(current))
        logger.info("eventstore.rotated")

    # ---- read / replay ----

    def replay(self, since: int = 0, limit: int = DEFAULT_REPLAY_LIMIT) -> dict[str, Any]:
        """Return up to `limit` events with seq > `since`.

        Walks rotated files from oldest to newest (KEEP_ROTATIONS..1) and then
        the current log. Stops once `limit` is reached.
        """
        limit = max(1, min(int(limit or DEFAULT_REPLAY_LIMIT), MAX_REPLAY_LIMIT))
        since = max(0, int(since or 0))
        out: list[dict[str, Any]] = []
        for path in self._ordered_paths():
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        seq = int(obj.get("seq", 0) or 0)
                        if seq <= since:
                            continue
                        out.append(obj)
                        if len(out) >= limit:
                            break
            except Exception:
                logger.exception("eventstore.replay_failed", path=str(path))
            if len(out) >= limit:
                break
        return {
            "events": out,
            "count": len(out),
            "latest_seq": self._seq,
            "truncated": len(out) >= limit,
        }

    def _ordered_paths(self) -> list[Path]:
        """Oldest → newest."""
        paths: list[Path] = []
        for i in range(KEEP_ROTATIONS, 0, -1):
            paths.append(_rotated_path(i))
        paths.append(events_path())
        return paths

    @property
    def latest_seq(self) -> int:
        return self._seq


# Module-level singleton — one journal per lampgo process.
_store: EventStore | None = None


def get_store() -> EventStore:
    global _store
    if _store is None:
        _store = EventStore()
    return _store
