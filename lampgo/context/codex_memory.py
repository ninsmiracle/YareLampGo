"""Fast, bounded access to Codex's distilled local memory summary."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")


class CodexMemorySummaryProvider:
    """Read one file, cache it, and select a small prompt-sized subset.

    No rollout logs, databases, embeddings, subprocesses, or network calls are
    allowed on this path.  A cheap mtime check keeps the cache fresh.
    """

    def __init__(self, path: Path | None = None) -> None:
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self.path = path or codex_home / "memories" / "memory_summary.md"
        self._mtime_ns = -1
        self._content = ""
        self._sections: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        if mtime_ns == self._mtime_ns:
            return
        with self._lock:
            try:
                current_mtime = self.path.stat().st_mtime_ns
                content = self.path.read_text(encoding="utf-8")
            except OSError:
                current_mtime = -1
                content = ""
            if current_mtime == self._mtime_ns:
                return
            self._mtime_ns = current_mtime
            self._content = content
            self._sections = self._split_sections(content)

    @staticmethod
    def _split_sections(content: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        title = "Overview"
        lines: list[str] = []
        for line in content.splitlines():
            if line.startswith("## "):
                if lines:
                    sections.append((title, "\n".join(lines).strip()))
                title = line[3:].strip() or "Untitled"
                lines = [line]
            else:
                lines.append(line)
        if lines:
            sections.append((title, "\n".join(lines).strip()))
        return [(heading, body) for heading, body in sections if body]

    @staticmethod
    def _terms(text: str) -> set[str]:
        terms = {token.lower() for token in _ASCII_WORD_RE.findall(text)}
        for run in _CJK_RUN_RE.findall(text):
            if len(run) == 1:
                terms.add(run)
                continue
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
        return terms

    def get_context(self, query: str = "", *, max_chars: int = 6000) -> str:
        self._refresh()
        if max_chars <= 0 or not self._sections:
            return ""

        query_terms = self._terms(query)
        ranked: list[tuple[int, int, str]] = []
        for index, (heading, body) in enumerate(self._sections):
            lower_heading = heading.lower()
            terms = self._terms(heading + "\n" + body)
            score = len(query_terms & terms) * 10
            if "user profile" in lower_heading or "user preferences" in lower_heading:
                score += 8
            ranked.append((score, -index, body))
        ranked.sort(reverse=True)

        selected: list[str] = []
        used = 0
        for _score, _index, body in ranked:
            remaining = max_chars - used
            if remaining <= 0:
                break
            chunk = body[:remaining].rstrip()
            if not chunk:
                continue
            selected.append(chunk)
            used += len(chunk) + 2
        return "\n\n".join(selected)[:max_chars].rstrip()
