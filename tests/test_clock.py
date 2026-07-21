from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from starlette.testclient import TestClient

from lampgo.clock import ClockController
from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


class _FakeLed:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.stop_calls = 0

    def show_clock(self, **payload) -> bool:
        self.calls.append(payload)
        return True

    def stop_clock(self) -> bool:
        self.stop_calls += 1
        return True


def test_clock_sends_current_minute_once_and_persists_style(tmp_path: Path) -> None:
    current = datetime(2026, 7, 21, 9, 8, tzinfo=timezone.utc)
    led = _FakeLed()
    clock = ClockController(led, path=tmp_path / "clock.json", now=lambda: current, brightness_ceiling=lambda: 40)

    shown = clock.show(color="#ff0088", brightness=80, effect="orbit")

    assert shown["ok"] is True
    assert shown["time"] == "09:08"
    assert led.calls == [{"hour": 9, "minute": 8, "color": "#ff0088", "brightness": 40, "effect": "orbit"}]
    assert clock.refresh()["sent"] is False

    current = datetime(2026, 7, 21, 9, 9, tzinfo=timezone.utc)
    assert clock.refresh()["sent"] is True
    assert led.calls[-1]["minute"] == 9

    restored = ClockController(_FakeLed(), path=tmp_path / "clock.json", now=lambda: current)
    assert restored.snapshot()["enabled"] is True
    assert restored.snapshot()["color"] == "#ff0088"
    assert restored.snapshot()["effect"] == "orbit"


def test_clock_stop_disables_future_updates(tmp_path: Path) -> None:
    led = _FakeLed()
    clock = ClockController(led, path=tmp_path / "clock.json", now=lambda: datetime(2026, 7, 21, 9, 8))
    clock.show()

    stopped = clock.stop()

    assert stopped["ok"] is True
    assert stopped["enabled"] is False
    assert led.stop_calls == 1
    assert clock.refresh()["sent"] is False


def test_clock_http_routes_return_backend_owned_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    config = LampgoConfig(device=DeviceConfig(motor_port="/dev/null"))
    gateway = WebGateway(LampgoServer(config))
    led = _FakeLed()
    gateway.server.clock = ClockController(
        led,
        path=tmp_path / "clock.json",
        now=lambda: datetime(2026, 7, 21, 18, 30),
    )

    with TestClient(gateway.app) as client:
        gateway.server._register_builtin_skills()
        assert gateway.server.registry.get("show_clock") is not None
        shown = client.post("/api/clock/show", json={"color": "#00ffcc", "brightness": 25, "effect": "blink"})
        state = client.get("/api/clock")
        stopped = client.post("/api/clock/stop")

    assert shown.status_code == 200
    assert shown.json()["result"]["time"] == "18:30"
    assert led.calls[-1]["effect"] == "blink"
    assert state.json()["result"]["enabled"] is True
    assert stopped.json()["result"]["enabled"] is False
