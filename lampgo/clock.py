"""Backend-owned LED clock state and minute-level device updates."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from lampgo.core.led import LEDController

CLOCK_EFFECTS = frozenset({"steady", "blink", "orbit"})
DEFAULT_CLOCK_COLOR = "#37d6ff"
DEFAULT_CLOCK_BRIGHTNESS = 32


def _clock_path() -> Path:
    root = Path(os.environ.get("LAMPGO_HOME", Path.home() / ".lampgo"))
    return root / "clock.json"


def _normalize_color(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) == 7 and text.startswith("#") and all(ch in "0123456789abcdef" for ch in text[1:]):
        return text
    return DEFAULT_CLOCK_COLOR


def _normalize_effect(value: Any) -> str:
    effect = str(value or "").strip().lower()
    return effect if effect in CLOCK_EFFECTS else "steady"


def _normalize_brightness(value: Any) -> int:
    try:
        return max(1, min(96, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_CLOCK_BRIGHTNESS


@dataclass
class ClockSettings:
    enabled: bool = False
    color: str = DEFAULT_CLOCK_COLOR
    brightness: int = DEFAULT_CLOCK_BRIGHTNESS
    effect: str = "steady"

    @classmethod
    def from_mapping(cls, value: Any) -> "ClockSettings":
        data = value if isinstance(value, dict) else {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            color=_normalize_color(data.get("color")),
            brightness=_normalize_brightness(data.get("brightness")),
            effect=_normalize_effect(data.get("effect")),
        )


class ClockController:
    """Keeps a durable clock preference and sends only minute changes to S3."""

    def __init__(
        self,
        led: LEDController,
        *,
        path: Path | None = None,
        now: Callable[[], datetime] | None = None,
        brightness_ceiling: Callable[[], int] | None = None,
    ) -> None:
        self._led = led
        self._path = path or _clock_path()
        self._now = now or (lambda: datetime.now().astimezone())
        self._brightness_ceiling = brightness_ceiling or (lambda: 96)
        self._settings = self._load()
        self._last_minute_key = ""
        self._last_sent_at = ""

    def _load(self) -> ClockSettings:
        try:
            return ClockSettings.from_mapping(json.loads(self._path.read_text(encoding="utf-8")))
        except Exception:
            return ClockSettings()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(self._settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or self._now()
        return {
            **asdict(self._settings),
            "time": current.strftime("%H:%M"),
            "timezone": current.tzname() or "local",
            "last_sent_at": self._last_sent_at or None,
        }

    def show(self, *, color: Any = None, brightness: Any = None, effect: Any = None) -> dict[str, Any]:
        if color is not None:
            self._settings.color = _normalize_color(color)
        if brightness is not None:
            self._settings.brightness = min(_normalize_brightness(brightness), _normalize_brightness(self._brightness_ceiling()))
        if effect is not None:
            self._settings.effect = _normalize_effect(effect)
        self._settings.enabled = True
        self._save()
        return self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        current = self._now()
        state = self.snapshot(current)
        if not self._settings.enabled:
            return {"ok": True, "sent": False, **state}

        minute_key = current.strftime("%Y%m%d%H%M")
        if not force and minute_key == self._last_minute_key:
            return {"ok": True, "sent": False, **state}

        ok = self._led.show_clock(
            hour=current.hour,
            minute=current.minute,
            color=self._settings.color,
            brightness=min(self._settings.brightness, _normalize_brightness(self._brightness_ceiling())),
            effect=self._settings.effect,
        )
        if ok:
            self._last_minute_key = minute_key
            self._last_sent_at = current.isoformat(timespec="seconds")
        return {"ok": ok, "sent": True, **self.snapshot(current)}

    def stop(self) -> dict[str, Any]:
        self._settings.enabled = False
        self._last_minute_key = ""
        self._save()
        ok = self._led.stop_clock()
        return {"ok": ok, "sent": True, **self.snapshot()}

    def deactivate(self) -> None:
        """Prevent a saved clock from reappearing after another LED feature wins."""
        if self._settings.enabled:
            self._settings.enabled = False
            self._last_minute_key = ""
            self._save()
