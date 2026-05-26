"""Compatibility matrix between ``LLMClient`` and the two wire formats.

We don't hit a real LLM here — the goal is to verify, for both
``message_type == "openai"`` and ``"anthropic"``:

1. The agent **tool list** we hand the model is correct for that envelope.
   Since ``web_search`` was refactored into an independent MiMo sub-service
   (see ``LLMConfig.web_search_*`` docstring + ``_resolve_web_search_api_key``),
   it's exposed for BOTH envelopes as long as we have a MiMo-capable key.
2. The **request body** we'd POST for a realistic multi-turn conversation
   survives the translation layer intact — history, parallel tool_calls,
   tool_results, and inline camera images all make it through.
3. **History** text turns are preserved in order in both envelopes.
4. The web_search sub-service's API key fallback rules (dedicated key
   wins; else reuse main key iff provider=mimo; else feature off).

These scenarios are the regression set for Anthropic support AND the
independent web-search refactor.  Pinning them in tests keeps the
migrations from silently re-breaking.
"""

from __future__ import annotations

import json

from lampgo.core.config import LLMConfig
from lampgo.perception import anthropic_adapter as A
from lampgo.perception.llm_client import (
    MIMO_WEB_SEARCH_BASE_URL,
    MIMO_WEB_SEARCH_MODEL,
    _build_agent_tools,
    _resolve_web_search_api_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_cfg(**overrides) -> LLMConfig:
    """Minimal LLMConfig; web_search is on and provider defaults to MiMo so
    the main ``api_key`` satisfies the web-search sub-service's fallback
    rule — this is the branch we want exercised in most tests.
    """
    defaults = dict(
        provider="mimo",
        api_key="test-key",
        api_base="https://api.example/v1",
        fast_model="mimo-v2.5",
        message_type="openai",
        web_search_enabled=True,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _tool_names(tools: list[dict]) -> list[str]:
    return [t["function"]["name"] for t in tools if t.get("type") == "function"]


def _anthropic_tool_names(tools: list[dict]) -> list[str]:
    return [t["name"] for t in tools]


def _build_conversation_messages() -> list[dict]:
    """A realistic multi-turn conversation that exercises every wrinkle we
    care about: history text turns, a new user turn with an inline image,
    an assistant reply with two parallel tool_calls, and two tool_result
    entries (one per call, as the OpenAI wire format emits them).
    """
    return [
        {"role": "system", "content": "You are lampgo."},
        # --- history: previous user/assistant pair, text-only -----------
        {"role": "user", "content": "开一下灯"},
        {"role": "assistant", "content": "好的，已经把灯打开啦~"},
        # --- current user turn: text + image ----------------------------
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "你在干嘛"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,AAAA"},
                },
            ],
        },
        # --- assistant calls two tools in parallel ----------------------
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_A",
                    "type": "function",
                    "function": {
                        "name": "set_expression",
                        "arguments": '{"expression": "smile"}',
                    },
                },
                {
                    "id": "call_B",
                    "type": "function",
                    "function": {
                        "name": "say",
                        "arguments": '{"text": "我在看你"}',
                    },
                },
            ],
        },
        # --- OpenAI wire format: two separate `role: tool` messages -----
        {"role": "tool", "tool_call_id": "call_A", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "call_B", "content": '{"ok": true}'},
    ]


# ---------------------------------------------------------------------------
# 1. Tool schema exposed to the model — web_search gating
# ---------------------------------------------------------------------------


def test_openai_mode_exposes_web_search_function_tool() -> None:
    cfg = _base_cfg(message_type="openai")
    tools = _build_agent_tools(skills=[], config=cfg)
    assert "web_search" in _tool_names(tools)


def test_anthropic_mode_also_exposes_web_search_function_tool() -> None:
    """After the independent-sub-service refactor, web_search is
    envelope-agnostic: the agent sees the same function tool regardless of
    ``message_type``, because the sub-service opens its own MiMo OpenAI-
    compat connection internally.
    """
    cfg = _base_cfg(message_type="anthropic")
    tools = _build_agent_tools(skills=[], config=cfg)
    assert "web_search" in _tool_names(tools)
    for name in ("say", "finish_response", "escalate_to_openclaw"):
        assert name in _tool_names(tools)


def test_web_search_hidden_when_feature_flag_off() -> None:
    cfg = _base_cfg(message_type="openai", web_search_enabled=False)
    assert "web_search" not in _tool_names(_build_agent_tools([], cfg))


def test_web_search_hidden_when_no_effective_mimo_key() -> None:
    """Non-MiMo provider + no dedicated web_search key = no tool exposed,
    because the sub-service would 401 on every call."""
    cfg = _base_cfg(
        provider="openai",
        api_key="openai-test-key",
        web_search_api_key="",
    )
    assert "web_search" not in _tool_names(_build_agent_tools([], cfg))


def test_web_search_exposed_when_non_mimo_provider_but_dedicated_key_set() -> None:
    """Main LLM on Anthropic / OpenAI + a separate MiMo key for search =
    everything works, because the sub-service is independent."""
    cfg = _base_cfg(
        provider="anthropic",
        api_key="anthropic-test-key",
        message_type="anthropic",
        web_search_api_key="mimo-key-xyz",
    )
    assert "web_search" in _tool_names(_build_agent_tools([], cfg))


# ---------------------------------------------------------------------------
# 1b. Web search sub-service key-resolution rules
# ---------------------------------------------------------------------------


def test_resolve_web_search_key_prefers_dedicated_over_main_key() -> None:
    cfg = _base_cfg(
        provider="mimo",
        api_key="main-mimo-key",
        web_search_api_key="dedicated-ws-key",
    )
    assert _resolve_web_search_api_key(cfg) == "dedicated-ws-key"


def test_resolve_web_search_key_falls_back_to_main_key_only_for_mimo() -> None:
    cfg_mimo = _base_cfg(provider="mimo", api_key="main-key", web_search_api_key="")
    cfg_openai = _base_cfg(provider="openai", api_key="main-key", web_search_api_key="")
    assert _resolve_web_search_api_key(cfg_mimo) == "main-key"
    assert _resolve_web_search_api_key(cfg_openai) == ""


def test_resolve_web_search_key_uses_provider_alias() -> None:
    """Legacy aliases like ``mimo`` must normalize to mimo so the main
    key gets reused correctly."""
    cfg = _base_cfg(provider="mimo", api_key="alias-main-key", web_search_api_key="")
    assert _resolve_web_search_api_key(cfg) == "alias-main-key"


def test_web_search_sub_service_endpoint_is_hardcoded() -> None:
    """The whole point of the sub-service: endpoint + model are NOT
    user-configurable. Guard against someone accidentally making them so.
    """
    assert MIMO_WEB_SEARCH_BASE_URL == "https://api.mimomimo.com/v1"
    assert MIMO_WEB_SEARCH_MODEL == "mimo-v2.5-pro"


# ---------------------------------------------------------------------------
# 2. Request body survives translation in both envelopes
# ---------------------------------------------------------------------------


def test_anthropic_body_preserves_history_order_and_shape() -> None:
    cfg = _base_cfg(message_type="anthropic")
    agent_tools = _build_agent_tools([], cfg)
    body = A.build_request_body(
        model=cfg.fast_model,
        openai_messages=_build_conversation_messages(),
        openai_tools=agent_tools,
        openai_tool_choice="auto",
        max_tokens=2048,
        temperature=0.3,
        stream=True,
    )

    # System prompt must have been pulled out.
    assert body["system"] == "You are lampgo."
    assert all(m.get("role") != "system" for m in body["messages"])

    roles = [m["role"] for m in body["messages"]]
    # History pair, current user (merged to single turn), assistant tool_uses,
    # merged tool_results.
    assert roles == ["user", "assistant", "user", "assistant", "user"]

    # History content preserved as text blocks, in order.
    assert body["messages"][0]["content"] == [{"type": "text", "text": "开一下灯"}]
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "好的，已经把灯打开啦~"}
    ]

    # Current user: text + image block.
    current_user = body["messages"][2]["content"]
    assert current_user[0] == {"type": "text", "text": "你在干嘛"}
    assert current_user[1]["type"] == "image"
    assert current_user[1]["source"]["type"] == "base64"
    assert current_user[1]["source"]["media_type"] == "image/jpeg"

    # Assistant parallel tool_uses.
    assistant_blocks = body["messages"][3]["content"]
    tool_uses = [b for b in assistant_blocks if b["type"] == "tool_use"]
    assert [b["id"] for b in tool_uses] == ["call_A", "call_B"]
    assert [b["name"] for b in tool_uses] == ["set_expression", "say"]
    # Arguments were ``json.loads``'d into dicts.
    assert tool_uses[0]["input"] == {"expression": "smile"}
    assert tool_uses[1]["input"] == {"text": "我在看你"}

    # Tool results merged into ONE following user message (this was the
    # actual 400 bug we fixed last round).
    tool_results = body["messages"][4]["content"]
    assert [b["type"] for b in tool_results] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in tool_results] == ["call_A", "call_B"]

    # Tools were translated; web_search now DOES appear (the Anthropic
    # call will receive it as a plain function tool — the sub-service
    # handles it out-of-band when the model calls it).
    assert "tools" in body
    for name in ("say", "finish_response", "web_search"):
        assert name in _anthropic_tool_names(body["tools"])

    # tool_choice=auto translated correctly.
    assert body["tool_choice"] == {"type": "auto"}
    assert body["stream"] is True


def test_anthropic_body_required_becomes_any() -> None:
    """Turn 1 uses ``tool_choice="required"``; make sure it survives."""
    cfg = _base_cfg(message_type="anthropic")
    body = A.build_request_body(
        model=cfg.fast_model,
        openai_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        openai_tools=_build_agent_tools([], cfg),
        openai_tool_choice="required",
        max_tokens=1024,
        temperature=0.3,
        stream=False,
    )
    assert body["tool_choice"] == {"type": "any"}
    assert "stream" not in body


def test_anthropic_body_forces_specific_function_choice() -> None:
    """When the agent loop force-picks ``finish_response`` after the model
    went silent-with-reasoning, the adapter must emit
    ``{"type":"tool","name":"finish_response"}``."""
    cfg = _base_cfg(message_type="anthropic")
    body = A.build_request_body(
        model=cfg.fast_model,
        openai_messages=[
            {"role": "user", "content": "anything"},
        ],
        openai_tools=_build_agent_tools([], cfg),
        openai_tool_choice={"type": "function", "function": {"name": "finish_response"}},
        max_tokens=1024,
        temperature=0.3,
        stream=False,
    )
    assert body["tool_choice"] == {"type": "tool", "name": "finish_response"}


# ---------------------------------------------------------------------------
# 3. Round-trip: anthropic response -> openai message -> replayed as history
# ---------------------------------------------------------------------------


def test_anthropic_response_round_trips_into_next_turn() -> None:
    """The agent loop stores the assistant reply back in ``messages`` and
    uses it on the next turn.  After translation+back the conversation
    must still be valid input to ``build_request_body``.
    """
    raw_anthropic = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "let me think"},
            {"type": "text", "text": "调皮的你"},
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "set_expression",
                "input": {"expression": "heart"},
            },
        ],
        "stop_reason": "tool_use",
    }
    msg = A.anthropic_response_to_openai_message(raw_anthropic)
    # Canonical OpenAI-shaped output.
    assert msg["content"] == "调皮的你"
    assert msg["reasoning_content"] == "let me think"
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "tu_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "set_expression"
    # Arguments are a JSON **string** so the agent loop's ``json.loads``
    # keeps working on both code paths.
    assert json.loads(tc["function"]["arguments"]) == {"expression": "heart"}

    # Feed it back as history + a tool result + a new user text, like the
    # agent loop would on turn 2 — the adapter must still produce a valid
    # Anthropic body.
    next_body = A.build_request_body(
        model="mimo-v2.5",
        openai_messages=[
            {"role": "system", "content": "You are lampgo."},
            {"role": "user", "content": "表情变一下"},
            {
                "role": "assistant",
                "content": msg["content"],
                "tool_calls": msg["tool_calls"],
                "reasoning_content": msg.get("reasoning_content", ""),
            },
            {"role": "tool", "tool_call_id": "tu_1", "content": '{"ok": true}'},
            {"role": "user", "content": "谢谢"},
        ],
        openai_tools=[],
        openai_tool_choice="auto",
        max_tokens=1024,
        temperature=0.3,
        stream=False,
    )
    roles = [m["role"] for m in next_body["messages"]]
    assert roles == ["user", "assistant", "user", "user"]
    # Assistant block contains both text and the tool_use.
    asst = next_body["messages"][1]["content"]
    assert any(b["type"] == "text" for b in asst)
    assert any(b["type"] == "tool_use" and b["id"] == "tu_1" for b in asst)
    # Tool result is its own user message (single result, no merge needed).
    assert next_body["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"ok": true}'}
    ]
    # Trailing real user turn preserved.
    assert next_body["messages"][3]["content"] == [{"type": "text", "text": "谢谢"}]


# ---------------------------------------------------------------------------
# 4. OpenAI mode sanity — request body shape
# ---------------------------------------------------------------------------


def test_openai_mode_agent_tools_match_openai_function_schema() -> None:
    """OpenAI's ``chat.completions`` expects tools shaped as
    ``{type:"function", function:{name, description, parameters}}``.  The
    LLM client builds them inline — verify the contract so nobody silently
    drops ``type: "function"`` and breaks the OpenAI path.
    """
    cfg = _base_cfg(message_type="openai")
    tools = _build_agent_tools([], cfg)
    assert tools, "agent tool list must not be empty"
    for tool in tools:
        assert tool.get("type") == "function"
        fn = tool.get("function") or {}
        assert isinstance(fn.get("name"), str) and fn["name"]
        assert "parameters" in fn
        params = fn["parameters"]
        assert params.get("type") == "object"
        assert "properties" in params
