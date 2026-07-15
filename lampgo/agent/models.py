"""Provider-neutral task models for external agent harnesses."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTask:
    task_id: str
    request_id: str
    user_text: str
    reason: str
    provider: str = "codex"
    provider_thread_id: str = ""
    workspace: str = ""
    sandbox: str = "read-only"
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    detail: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "user_text": self.user_text,
            "reason": self.reason,
            "provider": self.provider,
            "provider_thread_id": self.provider_thread_id,
            "workspace": self.workspace,
            "sandbox": self.sandbox,
            "context": dict(self.context),
            "status": self.status,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": list(self.events[-100:]),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentTask:
        return cls(
            task_id=str(raw["task_id"]),
            request_id=str(raw.get("request_id", "")),
            user_text=str(raw.get("user_text", "")),
            reason=str(raw.get("reason", "")),
            provider=str(raw.get("provider", "codex")),
            provider_thread_id=str(raw.get("provider_thread_id", "")),
            workspace=str(raw.get("workspace", "")),
            sandbox=str(raw.get("sandbox", "read-only")),
            context=dict(raw.get("context") or {}),
            status=str(raw.get("status", "queued")),
            detail=str(raw.get("detail", "")),
            created_at=float(raw.get("created_at", time.time())),
            updated_at=float(raw.get("updated_at", time.time())),
            events=list(raw.get("events") or []),
        )
