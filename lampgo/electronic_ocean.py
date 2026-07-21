"""Backend control plane for the S3-local electronic ocean renderer."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_OCEAN_COLOR = "#00b8e0"
OCEAN_DYNAMICS_PRESETS: dict[str, dict[str, int]] = {
    "soft": {
        "sensitivity_percent": 80,
        "tilt_percent": 70,
        "impact_percent": 45,
        "damping_percent": 170,
        "edge_highlight_percent": 60,
    },
    "standard": {
        "sensitivity_percent": 100,
        "tilt_percent": 100,
        "impact_percent": 100,
        "damping_percent": 130,
        "edge_highlight_percent": 75,
    },
    "violent": {
        "sensitivity_percent": 125,
        "tilt_percent": 135,
        "impact_percent": 165,
        "damping_percent": 100,
        "edge_highlight_percent": 95,
    },
}


def _ocean_path() -> Path:
    root = Path(os.environ.get("LAMPGO_HOME", Path.home() / ".lampgo"))
    return root / "electronic_ocean.json"


def _integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _color(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) == 7 and text.startswith("#") and all(ch in "0123456789abcdef" for ch in text[1:]):
        return text
    return DEFAULT_OCEAN_COLOR


def _dynamics(value: Any) -> str:
    name = str(value or "").strip().lower()
    return name if name in OCEAN_DYNAMICS_PRESETS else "standard"


@dataclass
class OceanSettings:
    enabled: bool = False
    dynamics: str = "standard"
    color: str = DEFAULT_OCEAN_COLOR
    brightness: int = 36
    fill_percent: int = 55
    sensitivity_percent: int = 100
    edge_highlight_percent: int = 75
    tilt_percent: int = 100
    impact_percent: int = 100
    damping_percent: int = 130

    @classmethod
    def from_mapping(cls, value: Any) -> OceanSettings:
        data = value if isinstance(value, dict) else {}
        dynamics = _dynamics(data.get("dynamics"))
        preset = OCEAN_DYNAMICS_PRESETS[dynamics]
        return cls(
            enabled=bool(data.get("enabled", False)),
            dynamics=dynamics,
            color=_color(data.get("color")),
            brightness=_integer(data.get("brightness"), 36, 1, 96),
            fill_percent=_integer(data.get("fill_percent"), 55, 20, 80),
            sensitivity_percent=_integer(
                data.get("sensitivity_percent"), preset["sensitivity_percent"], 25, 200
            ),
            edge_highlight_percent=_integer(
                data.get("edge_highlight_percent"), preset["edge_highlight_percent"], 0, 100
            ),
            tilt_percent=_integer(data.get("tilt_percent"), preset["tilt_percent"], 50, 160),
            impact_percent=_integer(data.get("impact_percent"), preset["impact_percent"], 0, 200),
            damping_percent=_integer(data.get("damping_percent"), preset["damping_percent"], 80, 200),
        )


class ElectronicOceanController:
    """Samples joint 5 at 10 Hz while S3 owns simulation and pixel rendering."""

    def __init__(
        self,
        esp32: Any,
        angle_source: Callable[[], float | None],
        *,
        path: Path | None = None,
        monotonic: Callable[[], float] | None = None,
        brightness_ceiling: Callable[[], int] | None = None,
    ) -> None:
        self._esp32 = esp32
        self._angle_source = angle_source
        self._path = path or _ocean_path()
        self._monotonic = monotonic or time.monotonic
        self._brightness_ceiling = brightness_ceiling or (lambda: 96)
        self._settings = self._load()
        self._baseline_deg = 0.0
        self._last_angle_deg = 0.0
        self._last_sample_at = 0.0
        self._filtered_velocity_dps = 0.0
        self._sequence = 0
        self._device_started = False
        self._last_sent_at = 0.0
        self._last_error: str | None = None
        self._joint_available = False

    def _load(self) -> OceanSettings:
        try:
            settings = OceanSettings.from_mapping(json.loads(self._path.read_text(encoding="utf-8")))
            settings.enabled = False
            return settings
        except Exception:
            return OceanSettings()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(self._settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_angle(self) -> float | None:
        try:
            value = self._angle_source()
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def snapshot(self) -> dict[str, Any]:
        now = self._monotonic()
        return {
            **asdict(self._settings),
            "joint": "wrist_pitch",
            "joint_available": self._joint_available,
            "baseline_deg": round(self._baseline_deg, 3),
            "angle_deg": round(self._last_angle_deg - self._baseline_deg, 3),
            "angular_velocity_dps": round(self._filtered_velocity_dps, 3),
            "sequence": self._sequence,
            "telemetry_hz": 10,
            "device_render_fps": 20,
            "last_sent_age_ms": round(max(0.0, now - self._last_sent_at) * 1000) if self._last_sent_at else None,
            "last_error": self._last_error,
        }

    def _start_payload(self) -> dict[str, Any]:
        return {
            "action": "start",
            "color": self._settings.color,
            "brightness": min(self._settings.brightness, _integer(self._brightness_ceiling(), 96, 1, 96)),
            "fill_percent": self._settings.fill_percent,
            "sensitivity_percent": self._settings.sensitivity_percent,
            "edge_highlight_percent": self._settings.edge_highlight_percent,
            "tilt_percent": self._settings.tilt_percent,
            "impact_percent": self._settings.impact_percent,
            "damping_percent": self._settings.damping_percent,
        }

    async def _post(self, payload: dict[str, Any], *, reason: str) -> tuple[bool, dict[str, Any]]:
        body = self._esp32.with_owner_auth(payload, reason=reason)
        status, response, _ = await self._esp32.proxy_post("/device/ocean", body)
        data = response if isinstance(response, dict) else {"raw": str(response)}
        ok = status < 400 and data.get("ok") is not False
        self._last_error = None if ok else str(data.get("error") or f"device returned {status}")
        return ok, data

    async def start(self, **values: Any) -> dict[str, Any]:
        current = asdict(self._settings)
        if values.get("dynamics") is not None:
            dynamics = _dynamics(values["dynamics"])
            for key, value in OCEAN_DYNAMICS_PRESETS[dynamics].items():
                if key not in values:
                    current[key] = value
        current.update({key: value for key, value in values.items() if value is not None})
        self._settings = OceanSettings.from_mapping(current)
        self._settings.enabled = True
        angle = self._read_angle()
        self._joint_available = angle is not None
        self._baseline_deg = angle if angle is not None else 0.0
        self._last_angle_deg = self._baseline_deg
        self._last_sample_at = self._monotonic()
        self._filtered_velocity_dps = 0.0
        self._sequence = 0
        self._device_started = False
        self._save()
        ok, device = await self._post(self._start_payload(), reason="electronic_ocean_start")
        self._device_started = ok
        if ok:
            self._last_sent_at = self._monotonic()
        return {"ok": ok, **self.snapshot(), "device": device}

    async def refresh(self) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": True, "sent": False, **self.snapshot()}
        if not self._device_started:
            ok, _ = await self._post(self._start_payload(), reason="electronic_ocean_resume")
            self._device_started = ok
            if not ok:
                return {"ok": False, "sent": False, **self.snapshot()}

        now = self._monotonic()
        angle = self._read_angle()
        self._joint_available = angle is not None
        if angle is None:
            angle = self._last_angle_deg
        elapsed = max(0.02, min(0.5, now - self._last_sample_at)) if self._last_sample_at else 0.1
        raw_velocity = (angle - self._last_angle_deg) / elapsed
        self._filtered_velocity_dps += (raw_velocity - self._filtered_velocity_dps) * 0.35
        self._last_angle_deg = angle
        self._last_sample_at = now
        self._sequence += 1
        payload = {
            "action": "input",
            "angle_deg": round(max(-35.0, min(35.0, angle - self._baseline_deg)), 3),
            "angular_velocity_dps": round(max(-180.0, min(180.0, self._filtered_velocity_dps)), 3),
            "sequence": self._sequence,
        }
        ok, device = await self._post(payload, reason="electronic_ocean_input")
        if ok:
            self._last_sent_at = now
        else:
            self._device_started = False
        return {"ok": ok, "sent": True, **self.snapshot(), "device": device}

    async def stop(self) -> dict[str, Any]:
        self._settings.enabled = False
        self._device_started = False
        self._save()
        ok, device = await self._post({"action": "stop"}, reason="electronic_ocean_stop")
        return {"ok": ok, **self.snapshot(), "device": device}

    def deactivate(self) -> None:
        """Stop telemetry when another LED mode has already won on the device."""
        if self._settings.enabled:
            self._settings.enabled = False
            self._device_started = False
            self._save()
