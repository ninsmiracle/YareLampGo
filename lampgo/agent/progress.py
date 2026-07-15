"""User-facing progress summaries for Codex JSONL events.

This module deliberately exposes only Codex commentary and concise action
summaries.  Raw reasoning payloads, encrypted reasoning, full command output,
and tool arguments are not suitable for the LampGo chat surface.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")


def _text(value: Any, *, limit: int = 240) -> str:
    if isinstance(value, list):
        value = " ".join(str(part) for part in value if part is not None)
    elif isinstance(value, dict):
        value = value.get("text") or value.get("summary") or ""
    cleaned = " ".join(str(value or "").split())
    cleaned = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[已隐藏]", cleaned)
    cleaned = _BEARER_RE.sub("Bearer [已隐藏]", cleaned)
    if len(cleaned) > limit:
        return cleaned[: max(1, limit - 1)].rstrip() + "…"
    return cleaned


def _item_state(event_type: str, item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").lower()
    if event_type.endswith("failed") or status in {"failed", "error", "declined"}:
        return "error"
    if event_type.endswith("completed") or status in {"completed", "success", "ok"}:
        return "done"
    return "active"


def _item_id(item: dict[str, Any], kind: str) -> str:
    raw = str(item.get("id") or item.get("item_id") or "").strip()
    return f"item:{raw}" if raw else f"item:{kind}"


def _summary_text(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if isinstance(summary, list):
        parts: list[str] = []
        for entry in summary:
            if isinstance(entry, dict):
                part = _text(entry.get("text") or entry.get("summary"), limit=400)
            else:
                part = _text(entry, limit=400)
            if part:
                parts.append(part)
        return _text(" ".join(parts), limit=500)
    return _text(summary, limit=500)


def summarize_codex_event(event: dict[str, Any]) -> dict[str, str] | None:
    """Return a safe, concise progress item for a Codex JSONL event."""

    event_type = str(event.get("type") or "event")
    if event_type == "thread.started":
        return {
            "id": "thread",
            "kind": "lifecycle",
            "summary": "Codex 已建立任务线程。",
            "state": "done",
            "event_type": event_type,
        }
    if event_type == "turn.started":
        return {
            "id": "turn",
            "kind": "lifecycle",
            "summary": "Codex 正在分析任务。",
            "state": "active",
            "event_type": event_type,
        }
    if event_type == "turn.completed":
        return {
            "id": "turn",
            "kind": "lifecycle",
            "summary": "Codex 已完成分析，正在整理结果。",
            "state": "done",
            "event_type": event_type,
        }

    item = event.get("item")
    if not isinstance(item, dict):
        return None
    kind = str(item.get("type") or "item")
    state = _item_state(event_type, item)
    progress_id = _item_id(item, kind)

    if kind == "agent_message":
        phase = str(item.get("phase") or "").lower()
        message = _text(item.get("text") or item.get("message"), limit=700)
        # Final answers are delivered as a separate chat message.  Older CLI
        # versions omit `phase`, so a long agent message is also treated as the
        # final answer rather than duplicated in the progress feed.
        if not message or phase in {"final", "final_answer"} or len(message) > 650:
            return None
        return {
            "id": progress_id,
            "kind": "commentary",
            "summary": message,
            "state": "done" if state == "done" else "active",
            "event_type": event_type,
        }

    if kind == "command_execution":
        command = item.get("command") or item.get("cmd")
        command_text = _text(command, limit=160)
        if "\n" in str(command or "") or len(str(command or "")) > 500:
            command_text = "本地命令"
        verb = "已执行" if state == "done" else "执行失败" if state == "error" else "正在执行"
        return {
            "id": progress_id,
            "kind": "command",
            "summary": f"{verb}：{command_text or '本地命令'}",
            "state": state,
            "event_type": event_type,
        }

    if kind == "web_search":
        query = _text(item.get("query") or item.get("queries"), limit=180)
        verb = "已搜索" if state == "done" else "搜索失败" if state == "error" else "正在搜索"
        return {
            "id": progress_id,
            "kind": "search",
            "summary": f"{verb}：{query or '相关资料'}",
            "state": state,
            "event_type": event_type,
        }

    if kind in {"mcp_tool_call", "tool_call", "function_call"}:
        server = _text(item.get("server"), limit=60)
        tool = _text(item.get("tool") or item.get("name"), limit=100)
        label = " / ".join(part for part in (server, tool) if part) or "本地工具"
        verb = "已调用工具" if state == "done" else "工具调用失败" if state == "error" else "正在调用工具"
        return {
            "id": progress_id,
            "kind": "tool",
            "summary": f"{verb}：{label}",
            "state": state,
            "event_type": event_type,
        }

    if kind == "file_change":
        changes = item.get("changes")
        paths: list[str] = []
        if isinstance(changes, list):
            for change in changes[:3]:
                if isinstance(change, dict):
                    path = _text(change.get("path"), limit=100)
                    if path:
                        paths.append(path)
        label = "、".join(paths) or "项目文件"
        verb = "已修改" if state == "done" else "修改失败" if state == "error" else "正在修改"
        return {
            "id": progress_id,
            "kind": "file",
            "summary": f"{verb}：{label}",
            "state": state,
            "event_type": event_type,
        }

    if kind == "reasoning":
        # Only an explicit plaintext summary is user-facing. Never inspect or
        # forward encrypted_content or raw reasoning bodies.
        summary = _summary_text(item)
        if not summary:
            return None
        return {
            "id": progress_id,
            "kind": "reasoning_summary",
            "summary": f"思路摘要：{summary}",
            "state": state,
            "event_type": event_type,
        }
    return None
