from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.electronic_ocean import ElectronicOceanController
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


class _FakeEsp32:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_next = False

    def with_owner_auth(self, payload: dict | None = None, *, reason: str = "") -> dict:
        return {**(payload or {}), "owner_id": "test-owner", "reason": reason}

    async def proxy_post(self, path: str, payload: dict):
        self.calls.append({"path": path, "payload": payload})
        if self.fail_next:
            self.fail_next = False
            return 502, {"ok": False, "error": "offline"}, "application/json"
        return 200, {"ok": True, "action": payload["action"]}, "application/json"


async def test_ocean_calibrates_current_wrist_and_sends_small_telemetry(tmp_path: Path) -> None:
    angle = 12.0
    now = 100.0
    esp32 = _FakeEsp32()
    ocean = ElectronicOceanController(
        esp32,
        lambda: angle,
        path=tmp_path / "ocean.json",
        monotonic=lambda: now,
        brightness_ceiling=lambda: 40,
    )

    started = await ocean.start(
        color="#12abef",
        brightness=80,
        fill_percent=60,
        sensitivity_percent=125,
        edge_highlight_percent=90,
    )
    assert started["ok"] is True
    assert started["baseline_deg"] == 12.0
    assert esp32.calls[0]["path"] == "/device/ocean"
    assert esp32.calls[0]["payload"] == {
        "action": "start",
        "color": "#12abef",
        "brightness": 40,
        "fill_percent": 60,
        "sensitivity_percent": 125,
        "edge_highlight_percent": 90,
        "tilt_percent": 100,
        "impact_percent": 100,
        "damping_percent": 130,
        "owner_id": "test-owner",
        "reason": "electronic_ocean_start",
    }

    angle = 17.0
    now = 100.1
    refreshed = await ocean.refresh()
    payload = esp32.calls[-1]["payload"]
    assert refreshed["ok"] is True
    assert payload["action"] == "input"
    assert payload["angle_deg"] == 5.0
    assert payload["angular_velocity_dps"] == 17.5
    assert payload["sequence"] == 1


async def test_ocean_violent_preset_applies_complete_dynamics_profile(tmp_path: Path) -> None:
    esp32 = _FakeEsp32()
    ocean = ElectronicOceanController(
        esp32,
        lambda: 0.0,
        path=tmp_path / "ocean.json",
    )

    started = await ocean.start(dynamics="violent")

    assert started["ok"] is True
    assert started["dynamics"] == "violent"
    assert esp32.calls[0]["payload"] == {
        "action": "start",
        "color": "#00b8e0",
        "brightness": 36,
        "fill_percent": 55,
        "sensitivity_percent": 125,
        "edge_highlight_percent": 95,
        "tilt_percent": 135,
        "impact_percent": 165,
        "damping_percent": 100,
        "owner_id": "test-owner",
        "reason": "electronic_ocean_start",
    }


async def test_ocean_coalesces_state_and_recovers_device_session(tmp_path: Path) -> None:
    angle = 0.0
    now = 10.0
    esp32 = _FakeEsp32()
    ocean = ElectronicOceanController(
        esp32,
        lambda: angle,
        path=tmp_path / "ocean.json",
        monotonic=lambda: now,
    )
    await ocean.start()

    esp32.fail_next = True
    angle = 2.0
    now += 0.1
    failed = await ocean.refresh()
    assert failed["ok"] is False
    assert failed["last_error"] == "offline"

    now += 0.1
    resumed = await ocean.refresh()
    assert resumed["ok"] is True
    assert [call["payload"]["action"] for call in esp32.calls[-2:]] == ["start", "input"]

    stopped = await ocean.stop()
    assert stopped["enabled"] is False
    assert esp32.calls[-1]["payload"]["action"] == "stop"

    restored = ElectronicOceanController(esp32, lambda: 0.0, path=tmp_path / "ocean.json")
    assert restored.snapshot()["enabled"] is False
    assert restored.snapshot()["color"] == "#00b8e0"


class _RouteOcean:
    def __init__(self) -> None:
        self.state = {"enabled": False, "color": "#00b8e0"}

    def snapshot(self) -> dict:
        return dict(self.state)

    async def refresh(self) -> dict:
        return {"ok": True, "sent": False, **self.state}

    async def start(self, **values) -> dict:
        self.state = {**self.state, **values, "enabled": True}
        return {"ok": True, **self.state}

    async def stop(self) -> dict:
        self.state["enabled"] = False
        return {"ok": True, **self.state}

    def deactivate(self) -> None:
        self.state["enabled"] = False


def test_ocean_http_routes_and_llm_skills_are_exposed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    ocean = _RouteOcean()
    server.electronic_ocean = ocean
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        server._register_builtin_skills()
        started = client.post(
            "/api/electronic-ocean/start",
            json={"color": "#22ccff", "fill_percent": 62},
        )
        state = client.get("/api/electronic-ocean")
        stopped = client.post("/api/electronic-ocean/stop")

    assert started.status_code == 200
    assert started.json()["result"]["enabled"] is True
    assert state.json()["result"]["fill_percent"] == 62
    assert stopped.json()["result"]["enabled"] is False
    assert server.registry.get("start_electronic_ocean") is not None
    assert server.registry.get("stop_electronic_ocean") is not None
