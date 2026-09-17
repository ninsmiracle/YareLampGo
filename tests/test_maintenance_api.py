import io
import zipfile
from unittest.mock import AsyncMock

from starlette.testclient import TestClient

from lampgo.core.config import LampgoConfig
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def test_maintenance_status_and_export_work_without_hardware(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(no_hw=True))
    with TestClient(WebGateway(server).app) as client:
        result = client.get("/api/maintenance")
        assert result.status_code == 200
        assert result.json()["result"]["phase"] == "idle"
        assert "runtime-" in result.json()["result"]["log_path"]
        rejected = client.post("/api/maintenance", json={"operation": "begin", "confirmed": True})
        assert rejected.status_code == 409
        assert server.maintenance.active is False
        malformed = client.post("/api/maintenance", json=[])
        assert malformed.status_code == 409
        download = client.get("/api/diagnostics/download")
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert "status.json" in archive.namelist()
            assert all("credentials" not in name for name in archive.namelist())
        page = client.get("/").text
        for button in ("btn-estop", "btn-p4-return-safe", "btn-p4-maintenance", "btn-record-motion-panel"):
            assert f'id="{button}"' in page


def test_failed_save_remains_visible_after_response_is_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(no_hw=True))
    server.maintenance.phase = "commit_uncertain"
    server.maintenance.command = AsyncMock(side_effect=RuntimeError("servo 1 register 31: readback failed"))
    with TestClient(WebGateway(server).app) as client:
        response = client.post("/api/maintenance", json={"operation": "commit"})
        assert response.status_code == 409
        assert response.json()["result"]["phase"] == "commit_uncertain"
        # A subsequent poll must recover the actual device error even if the
        # browser never received the POST response.
        state = client.get("/api/maintenance").json()["result"]
        assert state["error"] == "servo 1 register 31: readback failed"
