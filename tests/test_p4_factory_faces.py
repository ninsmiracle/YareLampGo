from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_expression_library import _create_eye, _gateway

from lampgo.expression_library import (
    ExpressionLibraryError,
    expression_capabilities,
    resolve_expression,
    save_expression_preset,
)
from lampgo.factory_faces import FACES, P4_FS_BYTES, RECORDING_FACES
from lampgo.recordings import list_recording_catalog


@pytest.mark.parametrize("face", FACES, ids=lambda x: x["id"])
def test_factory_recipe_and_pixels(monkeypatch, tmp_path, face):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    recipe = resolve_expression({"preset_id": face["id"], "duration_ms": 6000})
    assert recipe["eye_storage_clip_id"] == recipe["led_effect_id"] == face["id"]
    assert recipe["led_effect"]["mode"] == face["mode"]
    assert recipe["factory_eye"] and recipe["duration_ms"] == 3000
    assert expression_capabilities("esp32-p4")["eyes"]["used_bytes"] == 0
    with pytest.raises(ExpressionLibraryError, match="factory"):
        save_expression_preset({"preset_id": face["id"], "eye_clip_id": face["id"]})


def test_all_shipped_recordings_have_semantic_pairs_and_overrides_win(tmp_path):
    source = Path(__file__).parents[1] / "assets" / "recordings"
    for path in source.glob("*.csv"):
        (tmp_path / path.name).write_text("")
    catalog = list_recording_catalog(tmp_path)
    assert {x["name"] for x in catalog} == set(RECORDING_FACES)
    assert all(x["expression_preset"] == RECORDING_FACES[x["name"]] for x in catalog)
    override = tmp_path / "user" / "overrides"
    override.mkdir(parents=True)
    (override / "害羞.txt").write_text("expression_preset=my_own\n")
    (tmp_path / "user" / "睡觉.csv").write_text("")
    by_name = {x["name"]: x for x in list_recording_catalog(tmp_path)}
    assert by_name["害羞"]["expression_preset"] == "my_own"
    assert by_name["睡觉"]["expression_preset"] == ""


def test_storage_contract_has_worst_case_margin(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    capacity = expression_capabilities("esp32-p4")
    eye = capacity["eyes"]
    led = capacity["led_effects"]
    assert eye["max_count"] == 16 and eye["factory_count"] == 14
    worst = eye["max_count"] * eye["single_max_bytes"] + led["max_custom_count"] * led["single_max_bytes"]
    assert P4_FS_BYTES - worst - eye["staging_bytes"] - eye["reserved_bytes"] == 1048576
    assert expression_capabilities()["eyes"]["max_count"] == 5


def test_factory_play_requires_new_p4_and_never_uploads(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    sent = []

    async def post(path, payload):
        sent.append((path, payload))
        return 200, {"ok": True, "accepted": True}, "application/json"

    async def forbidden(*args, **kwargs):
        raise AssertionError("factory faces must not upload assets")

    monkeypatch.setattr(gateway.server.esp32, "proxy_post", post)
    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", forbidden)
    monkeypatch.setattr(gateway.server.esp32, "get_status", lambda: {"device": {"platform": "esp32-p4"}})
    with TestClient(gateway.app) as client:
        response = client.post("/api/expressions/play", json={"preset_id": "p4_shy"})
        assert response.status_code == 409 and not sent
        monkeypatch.setattr(
            gateway.server.esp32,
            "get_status",
            lambda: {"device": {"platform": "esp32-p4", "factory_face_version": "p4-face-v1"}},
        )
        response = client.post("/api/expressions/play", json={"preset_id": "p4_shy"})
        assert response.status_code == 200
    assert sent[-1][1]["eye_clip_id"] == sent[-1][1]["led_effect_id"] == "p4_shy"


def test_upload_accepts_storage_ack_without_false_display_proof(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    _create_eye("test_eye")
    monkeypatch.setattr(gateway.server.esp32, "get_status", lambda: {"device": {"platform": "esp32-p4"}})

    async def uploaded(*args, **kwargs):
        return 200, {"ok": True, "asset_stored": True, "display_confirmed": False}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", uploaded)
    with TestClient(gateway.app) as client:
        response = client.post("/api/eyes/test_eye/sync")
        assert response.status_code == 200, response.text


def test_tool_playback_uses_factory_binding_when_only_name_is_given(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from test_recording_expressions import _FakeLed, _FakeMotion, _write_recording_csv

    from lampgo.skills.builtin.playback_skills import PlayRecordingSkill

    _write_recording_csv(tmp_path / "害羞.csv")
    led, motion = _FakeLed(), _FakeMotion()
    result = asyncio.run(PlayRecordingSkill(tmp_path).execute(SimpleNamespace(led=led, motion=motion), name="害羞"))
    assert result.status == "ok"
    assert led.preset_calls == [("p4_shy", {"playback": "loop"})]
    assert led.stop_calls == 1


def test_legacy_hardware_uses_semantic_fallback_not_new_mode_id(monkeypatch):
    from lampgo.core.config import LEDConfig
    from lampgo.core.led import LEDController

    controller = LEDController(LEDConfig(port=""))
    modes = []
    monkeypatch.setattr(controller, "_active_device_is_p4", lambda: False)
    monkeypatch.setattr(controller, "set_mode", lambda mode: modes.append(mode) or True)
    ok, composition = controller.play_expression("p4_shy")
    assert ok and modes == ["blush"] and composition["factory_eye"]
