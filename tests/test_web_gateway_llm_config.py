from __future__ import annotations

import asyncio
import json

from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.perception import llm_client as llm_client_module
from lampgo.perception.llm_client import LLMClient
from lampgo.perception.router import IntentRouter, IntentType
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def test_llm_config_get_normalizes_legacy_provider_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    server.config.llm.provider = "mimo"
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.get("/api/config/llm")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["provider"] == "mimo"


def test_llm_config_post_persists_canonical_provider_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    monkeypatch.setattr(server, "reload_llm_client", lambda: None)
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/llm",
            json={
                "validate": False,
                "provider": "mimo",
                "api_base": "",
                "model": "mimo-v2-omni",
                "fast_model": "mimo-v2-omni",
                "message_type": "openai",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["provider"] == "mimo"
    assert server.config.llm.provider == "mimo"
    config_text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'provider = "mimo"' in config_text
    assert 'provider = "mimo-anthropic"' not in config_text


def test_mimo_provider_post_enables_web_search_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    server.config.llm.provider = "openai"
    server.config.llm.web_search_enabled = False
    monkeypatch.setattr(server, "reload_llm_client", lambda: None)
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/llm",
            json={
                "validate": False,
                "provider": "mimo",
                "api_base": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.5",
                "fast_model": "mimo-v2.5",
                "message_type": "openai",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["web_search_enabled"] is True
    assert server.config.llm.web_search_enabled is True


def test_llm_config_enable_thinking_defaults_off_and_persists(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    monkeypatch.setattr(server, "reload_llm_client", lambda: None)
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.get("/api/config/llm")
        assert response.status_code == 200
        assert response.json()["result"]["enable_thinking"] is False

        response = client.post(
            "/api/config/llm",
            json={"validate": False, "enable_thinking": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["enable_thinking"] is True
    assert server.config.llm.enable_thinking is True
    config_text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "enable_thinking = true" in config_text


def test_provider_presets_expose_per_format_base_urls(monkeypatch, tmp_path):
    """Presets must declare api_urls keyed by message_type so the frontend
    can auto-flip Base URL when the user toggles OpenAI ↔ Anthropic
    without breaking on older ``base_url``-only readers."""
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.get("/api/config/llm")
    presets = response.json()["result"]["provider_presets"]

    mimo = presets["mimo"]
    # Both endpoints are listed (this is the whole point of the schema).
    assert mimo["api_urls"]["openai"] == "https://api.xiaomimimo.com/v1"
    assert mimo["api_urls"]["anthropic"] == "https://api.xiaomimimo.com/anthropic/v1"
    assert mimo["default_message_type"] == "openai"
    # Legacy mirror kept for older callers that still read base_url.
    assert mimo["base_url"] == mimo["api_urls"]["openai"]

    # True Anthropic only offers one endpoint — api_urls must reflect that.
    assert "openai" not in presets["anthropic"]["api_urls"]
    assert presets["anthropic"]["default_message_type"] == "anthropic"
    # OpenAI is the symmetric case.
    assert "anthropic" not in presets["openai"]["api_urls"]

    # The short-lived `mimo-anthropic` preset MUST be gone — superseded
    # by the (mimo, anthropic) combo.  Leaving it visible would re-
    # introduce the "pick the wrong thing" UX trap the split fixed.
    assert "mimo-anthropic" not in presets


def test_llm_config_normalizes_mimo_anthropic_alias_to_mimo(monkeypatch, tmp_path):
    """A user who saved `provider = "mimo-anthropic"` during the brief
    window that preset existed must see their config normalised to the
    canonical `mimo` — their `message_type: "anthropic"` still kicks
    them onto the Anthropic endpoint automatically."""
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    server.config.llm.provider = "mimo-anthropic"
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.get("/api/config/llm")
    assert response.json()["result"]["provider"] == "mimo"


def test_web_search_subset_post_preserves_main_llm_fields(monkeypatch, tmp_path):
    """Legacy/API callers may still PATCH only ``web_search_*`` fields.

    The handler must treat omitted main-LLM fields as "keep existing" —
    otherwise saving web-search settings would silently reset ``api_base``
    to empty and ``message_type`` to ``"openai"``, kicking Anthropic users
    off their chosen endpoint.
    """
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    # Simulate a user already on MiMo Anthropic.
    server.config.llm.provider = "mimo"
    server.config.llm.message_type = "anthropic"
    server.config.llm.api_base = "https://api.xiaomimimo.com/anthropic/v1"
    server.config.llm.model = "mimo-v2.5"
    server.config.llm.fast_model = "mimo-v2.5"
    server.config.llm.api_key = "existing-test-key"
    monkeypatch.setattr(server, "reload_llm_client", lambda: None)
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/llm",
            json={
                "validate": False,
                "web_search_enabled": True,
                "web_search_force": True,
                "web_search_limit": 5,
                "web_search_max_keyword": 4,
                "web_search_country": "China",
                "web_search_region": "Hubei",
                "web_search_city": "Wuhan",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True

    # Main LLM fields must be untouched.
    assert server.config.llm.provider == "mimo"
    assert server.config.llm.message_type == "anthropic"
    assert server.config.llm.api_base == "https://api.xiaomimimo.com/anthropic/v1"
    assert server.config.llm.model == "mimo-v2.5"
    assert server.config.llm.fast_model == "mimo-v2.5"
    assert server.config.llm.api_key == "existing-test-key"

    # And the web-search fields were actually applied.
    assert server.config.llm.web_search_enabled is True
    assert server.config.llm.web_search_force is True
    assert server.config.llm.web_search_limit == 5
    assert server.config.llm.web_search_max_keyword == 4
    assert server.config.llm.web_search_country == "China"
    assert server.config.llm.web_search_region == "Hubei"
    assert server.config.llm.web_search_city == "Wuhan"


def test_web_search_api_key_persists_to_credentials(monkeypatch, tmp_path):
    """Dedicated MiMo web-search key must land in credentials.json under
    its own slot — not in config.toml, and not mixed with llm_api_key."""
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    server.config.llm.provider = "openai"
    server.config.llm.api_key = "openai-main-test-key"
    monkeypatch.setattr(server, "reload_llm_client", lambda: None)
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/llm",
            json={
                "validate": False,
                "web_search_api_key": "mimo-ws-secret",
            },
        )

    assert response.status_code == 200
    assert server.config.llm.web_search_api_key == "mimo-ws-secret"
    # Main key untouched.
    assert server.config.llm.api_key == "openai-main-test-key"

    import json
    creds = json.loads((tmp_path / "credentials.json").read_text(encoding="utf-8"))
    assert creds.get("llm_web_search_api_key") == "mimo-ws-secret"
    assert creds.get("llm_api_key") in (None, "openai-main-test-key")


def test_repeated_goodbye_is_keyword_chat() -> None:
    intent = IntentRouter().route("再见。再见。")

    assert intent.intent_type == IntentType.CHAT
    assert intent.end_conversation is True
    assert intent.matched_keyword == "再见"


async def test_llm_request_failure_returns_chat_not_openclaw_handoff(monkeypatch) -> None:
    client = LLMClient(
        LampgoConfig().llm.model_copy(
            update={
                "provider": "mimo",
                "api_key": "test-key",
                "api_base": "https://api.example/v1",
                "fast_model": "mimo-v2.5",
            }
        ),
        skill_specs=[],
    )
    seen_kwargs = {}

    async def fake_stream_chat_completion(**kwargs):
        seen_kwargs.update(kwargs)
        return None

    async def execute_tool(*args, **kwargs):
        raise AssertionError("request failure should not execute tools")

    monkeypatch.setattr(client, "_stream_chat_completion", fake_stream_chat_completion)

    result = await client.run_agent_loop("你在干嘛？", execute_tool=execute_tool)

    assert result.intent_type == "chat"
    assert result.stop_reason == "request_failed"
    assert result.response
    assert seen_kwargs["enable_thinking"] is False


async def test_llm_request_forwards_enable_thinking(monkeypatch) -> None:
    client = LLMClient(
        LampgoConfig().llm.model_copy(
            update={
                "provider": "mimo",
                "api_key": "test-key",
                "api_base": "https://api.example/v1",
                "fast_model": "mimo-v2.5",
            }
        ),
        skill_specs=[],
    )
    seen_kwargs = {}

    async def fake_stream_chat_completion(**kwargs):
        seen_kwargs.update(kwargs)
        return {"content": "好"}

    async def execute_tool(*args, **kwargs):
        raise AssertionError("content response should not execute tools")

    monkeypatch.setattr(client, "_stream_chat_completion", fake_stream_chat_completion)

    result = await client.run_agent_loop("你在干嘛？", execute_tool=execute_tool, enable_thinking=True)

    assert result.intent_type == "chat"
    assert result.response == "好"
    assert seen_kwargs["enable_thinking"] is True


async def test_agent_say_tts_tasks_are_awaited_before_finish(monkeypatch) -> None:
    client = LLMClient(
        LampgoConfig().llm.model_copy(
            update={
                "provider": "mimo",
                "api_key": "test-key",
                "api_base": "https://api.example/v1",
                "fast_model": "mimo-v2.5",
            }
        ),
        skill_specs=[],
    )
    original_sleep = asyncio.sleep
    events: list[str] = []

    async def fake_stream_chat_completion(**kwargs):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "say", "arguments": json.dumps({"text": "第一句"})},
                },
                {
                    "id": "call_2",
                    "function": {"name": "say", "arguments": json.dumps({"text": "第二句"})},
                },
                {
                    "id": "call_3",
                    "function": {"name": "finish_response", "arguments": json.dumps({"message": ""})},
                },
            ],
        }

    async def execute_tool(*args, **kwargs):
        raise AssertionError("say and finish_response should not execute external tools")

    async def on_progress(stage: str, message: str, source: str):
        if stage != "llm_narration":
            return None
        events.append(f"progress:{message}")

        async def tts_job() -> None:
            events.append(f"start:{message}")
            await original_sleep(0)
            events.append(f"end:{message}")

        return asyncio.create_task(tts_job())

    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(client, "_stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", fake_sleep)

    result = await client.run_agent_loop("连说两句", execute_tool=execute_tool, on_progress=on_progress)

    assert result.stop_reason == "finish_response"
    assert events.index("progress:第一句") < events.index("progress:第二句")
    assert events[-2:] == ["end:第一句", "end:第二句"]


async def test_server_tts_for_web_serializes_concurrent_requests(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    original_sleep = asyncio.sleep
    events: list[str] = []

    async def fake_tts_for_web_locked(text: str, request_id: str) -> None:
        events.append(f"start:{text}")
        await original_sleep(0)
        events.append(f"end:{text}")

    monkeypatch.setattr(server, "_tts_for_web_locked", fake_tts_for_web_locked)

    await asyncio.gather(
        server._tts_for_web("第一句", "r1"),
        server._tts_for_web("第二句", "r2"),
    )

    assert events == ["start:第一句", "end:第一句", "start:第二句", "end:第二句"]
