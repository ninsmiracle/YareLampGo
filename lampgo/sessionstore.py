"""Server-side persistence for chat sessions.

Stores the full session snapshot (what the frontend used to keep only in
localStorage) into `~/.lampgo/sessions.json`, so that:

1. Restarting the lampgo process does not lose chat history (frontend would
   still have it, but the server now becomes the shared source of truth).
2. Switching browsers (Chrome ↔ Safari) shows the same history, because the
   frontend boots by fetching from the server first.

Schema (JSON):
    {
      "version": 1,
      "active_session_id": "s_..." | null,
      "updated_at": 1716000000000,
      "sessions": [
        {
          "id": "s_...",
          "title": "...",
          "messages": [ { "role": "user"|"assistant"|"system", "text": "...",
                          "ts": 17160..., "meta": { ... } } ],
          "createdAt": 17160...,
          "updatedAt": 17160...
        },
        ...
      ]
    }

Writes are atomic (tmp + rename). Callers should treat the snapshot as an
opaque blob — the frontend owns the schema details, we just round-trip it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from lampgo.personastore import lampgo_home

logger = structlog.get_logger(__name__)

SCHEMA_VERSION = 1
MAX_SESSIONS = 40
MAX_MESSAGES_PER_SESSION = 2000


def sessions_path() -> Path:
    return lampgo_home() / "sessions.json"


def _empty_snapshot() -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "active_session_id": None,
        "updated_at": int(time.time() * 1000),
        "sessions": [],
    }


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, separators=(",", ":"))
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def load_snapshot() -> dict[str, Any]:
    """Return the persisted snapshot, or an empty scaffold if missing/corrupt."""
    p = sessions_path()
    if not p.exists():
        return _empty_snapshot()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("sessionstore.load_corrupt", path=str(p))
        return _empty_snapshot()
    if not isinstance(data, dict):
        return _empty_snapshot()
    # Fill missing fields conservatively.
    data.setdefault("version", SCHEMA_VERSION)
    data.setdefault("active_session_id", None)
    data.setdefault("updated_at", int(time.time() * 1000))
    data.setdefault("sessions", [])
    if not isinstance(data["sessions"], list):
        data["sessions"] = []
    return data


def _sanitize_session(raw: Any) -> dict[str, Any] | None:
    """Accept a session dict from the frontend, strip unexpected types, cap size."""
    if not isinstance(raw, dict):
        return None
    sid = raw.get("id")
    if not isinstance(sid, str) or not sid:
        return None
    title = raw.get("title")
    if not isinstance(title, str):
        title = "新会话"
    messages = raw.get("messages")
    if not isinstance(messages, list):
        messages = []
    cleaned_messages: list[dict[str, Any]] = []
    for m in messages[-MAX_MESSAGES_PER_SESSION:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        text = m.get("text")
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        entry: dict[str, Any] = {"role": role, "text": text}
        ts = m.get("ts")
        if isinstance(ts, (int, float)):
            entry["ts"] = int(ts)
        meta = m.get("meta")
        if isinstance(meta, dict):
            entry["meta"] = meta
        cleaned_messages.append(entry)
    out: dict[str, Any] = {
        "id": sid,
        "title": title[:200],
        "messages": cleaned_messages,
    }
    for k in ("createdAt", "updatedAt"):
        v = raw.get(k)
        if isinstance(v, (int, float)):
            out[k] = int(v)
    # Pass through a small whitelist of extra keys the frontend may attach.
    for k in ("summarized", "lastActivityAt"):
        v = raw.get(k)
        if v is not None:
            out[k] = v
    return out


def save_snapshot(snapshot: Any) -> dict[str, Any]:
    """Validate + persist a whole sessions snapshot. Returns the stored snapshot."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    raw_sessions = snapshot.get("sessions")
    if not isinstance(raw_sessions, list):
        raw_sessions = []
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in raw_sessions[:MAX_SESSIONS]:
        out = _sanitize_session(s)
        if out is None:
            continue
        if out["id"] in seen:
            continue
        seen.add(out["id"])
        cleaned.append(out)

    active = snapshot.get("active_session_id")
    if not isinstance(active, str) or active not in seen:
        active = None

    data = {
        "version": SCHEMA_VERSION,
        "active_session_id": active,
        "updated_at": int(time.time() * 1000),
        "sessions": cleaned,
    }
    _atomic_write_json(sessions_path(), data)
    logger.info(
        "sessionstore.saved",
        count=len(cleaned),
        active=active,
        path=str(sessions_path()),
    )
    return data


def delete_session(session_id: str) -> dict[str, Any]:
    """Remove a single session by id, return the updated snapshot."""
    snap = load_snapshot()
    before = len(snap.get("sessions", []))
    snap["sessions"] = [
        s for s in snap.get("sessions", []) if s.get("id") != session_id
    ]
    if snap.get("active_session_id") == session_id:
        snap["active_session_id"] = None
    snap["updated_at"] = int(time.time() * 1000)
    _atomic_write_json(sessions_path(), snap)
    logger.info(
        "sessionstore.deleted",
        session_id=session_id,
        removed=before - len(snap["sessions"]),
    )
    return snap


def clear_all() -> dict[str, Any]:
    """Wipe all sessions (keeps file, writes empty snapshot)."""
    snap = _empty_snapshot()
    _atomic_write_json(sessions_path(), snap)
    return snap
