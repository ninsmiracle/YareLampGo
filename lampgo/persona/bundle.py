"""Runtime snapshot of lampgo persona + memory for prompt injection.

Loading is cached for up to 60 seconds (or until `invalidate_bundles()` is called
after a write through the REST API). Cache misses are cheap — just disk reads of
a few small markdown files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from lampgo import personastore


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
    openclaw_core: str = ""
    openclaw_recent: list[tuple[str, str]] = field(default_factory=list)

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
        if self.openclaw_core.strip() or self.openclaw_recent:
            oc_chunks: list[str] = []
            if self.openclaw_core.strip():
                oc_chunks.append("#### OpenClaw core\n" + self.openclaw_core.strip())
            for d, content in self.openclaw_recent:
                if not content.strip():
                    continue
                oc_chunks.append(f"#### OpenClaw {d}\n{content.strip()}")
            if oc_chunks:
                chunks.append("### Shared OpenClaw memory\n" + "\n\n".join(oc_chunks))
        if not chunks:
            return ""
        return (
            "--- MEMORY ---\n"
            + "\n\n".join(chunks)
            + "\n--- END MEMORY ---\n"
        )


_CACHE: dict[str, Any] = {"mtime": 0.0, "persona": None, "memory": None, "share_oc": None}
_TTL_S = 60.0


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


def _load_memory(*, share_openclaw: bool) -> MemoryBundle:
    core = personastore.read_memory_core()
    recent = personastore.recent_memory_days(days=3)
    oc_core = ""
    oc_recent: list[tuple[str, str]] = []
    if share_openclaw:
        oc = personastore.openclaw_memory_preview(days=3)
        if oc.get("available"):
            oc_core = oc.get("core") or ""
            oc_recent = list(oc.get("recent_days") or [])
    return MemoryBundle(
        core=core,
        recent_days=recent,
        openclaw_core=oc_core,
        openclaw_recent=oc_recent,
    )


def load_bundles(cfg: Any = None, *, now: float | None = None) -> tuple[PersonaBundle, MemoryBundle]:
    """Return cached persona + memory bundles. `cfg` may be a LampgoConfig."""
    if cfg is not None and hasattr(cfg, "share_openclaw_memory"):
        share_oc = bool(cfg.share_openclaw_memory)
    else:
        overrides = personastore.get_overrides_toml() or {}
        val = overrides.get("share_openclaw_memory")
        share_oc = bool(val) if val is not None else True
    now_ts = now if now is not None else time.monotonic()
    cached = _CACHE
    if (
        cached["persona"] is not None
        and cached["memory"] is not None
        and cached["share_oc"] == share_oc
        and (now_ts - cached["mtime"]) < _TTL_S
    ):
        return cached["persona"], cached["memory"]
    persona = _load_persona()
    memory = _load_memory(share_openclaw=share_oc)
    _CACHE["persona"] = persona
    _CACHE["memory"] = memory
    _CACHE["share_oc"] = share_oc
    _CACHE["mtime"] = now_ts
    return persona, memory
