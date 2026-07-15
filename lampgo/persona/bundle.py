"""Runtime snapshot of lampgo persona + memory for prompt injection.

Loading is cached for up to 60 seconds (or until `invalidate_bundles()` is called
after a write through the REST API). Cache misses are cheap — just disk reads of
a few small markdown files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from lampgo import personastore
from lampgo.context.codex_memory import CodexMemorySummaryProvider


@dataclass
class PersonaBundle:
    soul: str = ""
    agents: str = ""
    profile: str = ""

    def render(self) -> str:
        """Render a prompt-ready markdown block. Returns empty string if all blank."""
        chunks: list[str] = []
        if self.soul.strip():
            chunks.append("## Identity (SOUL.md)\n" + self.soul.strip())
        if self.agents.strip():
            chunks.append("## Behavior (AGENTS.md)\n" + self.agents.strip())
        if self.profile.strip():
            chunks.append("## User profile (PROFILE.md)\n" + self.profile.strip())
        if not chunks:
            return ""
        return (
            "--- PERSONA (from ~/.lampgo/*.md, user-authored) ---\n"
            + "\n\n".join(chunks)
            + "\n--- END PERSONA ---\n"
        )


@dataclass
class MemoryBundle:
    core: str = ""
    recent_days: list[tuple[str, str]] = field(default_factory=list)
    codex_summary: str = ""

    def render(self) -> str:
        chunks: list[str] = []
        if self.core.strip():
            chunks.append("### Core memory (MEMORY.md)\n" + self.core.strip())
        if self.recent_days:
            daily = []
            for d, content in self.recent_days:
                if not content.strip():
                    continue
                daily.append(f"#### {d}\n{content.strip()}")
            if daily:
                chunks.append("### Recent daily notes\n" + "\n\n".join(daily))
        if self.codex_summary.strip():
            chunks.append("### Relevant Codex memory summary\n" + self.codex_summary.strip())
        if not chunks:
            return ""
        return (
            "--- MEMORY ---\n"
            + "\n\n".join(chunks)
            + "\n--- END MEMORY ---\n"
        )


_CACHE: dict[str, Any] = {"mtime": 0.0, "persona": None, "memory": None}
_TTL_S = 60.0
_CODEX_MEMORY = CodexMemorySummaryProvider()


def invalidate_bundles() -> None:
    _CACHE["mtime"] = 0.0
    _CACHE["persona"] = None
    _CACHE["memory"] = None


def _load_persona() -> PersonaBundle:
    files = personastore.read_all_personas()
    return PersonaBundle(
        soul=files.get("SOUL", ""),
        agents=files.get("AGENTS", ""),
        profile=files.get("PROFILE", ""),
    )


def _load_memory() -> MemoryBundle:
    core = personastore.read_memory_core()
    recent = personastore.recent_memory_days(days=3)
    return MemoryBundle(
        core=core,
        recent_days=recent,
    )


def load_bundles(
    cfg: Any = None,
    *,
    query: str = "",
    now: float | None = None,
) -> tuple[PersonaBundle, MemoryBundle]:
    """Return cached persona + memory bundles. `cfg` may be a LampgoConfig."""
    if cfg is not None and hasattr(cfg, "share_codex_memory"):
        share_codex = bool(cfg.share_codex_memory)
    else:
        overrides = personastore.get_overrides_toml() or {}
        val = overrides.get("share_codex_memory")
        share_codex = bool(val) if val is not None else True
    now_ts = now if now is not None else time.monotonic()
    cached = _CACHE
    if (
        cached["persona"] is not None
        and cached["memory"] is not None
        and (now_ts - cached["mtime"]) < _TTL_S
    ):
        memory = cached["memory"]
        codex_summary = _CODEX_MEMORY.get_context(query, max_chars=6000) if share_codex else ""
        return cached["persona"], replace(memory, codex_summary=codex_summary)
    persona = _load_persona()
    memory = _load_memory()
    _CACHE["persona"] = persona
    _CACHE["memory"] = memory
    _CACHE["mtime"] = now_ts
    codex_summary = _CODEX_MEMORY.get_context(query, max_chars=6000) if share_codex else ""
    return persona, replace(memory, codex_summary=codex_summary)
