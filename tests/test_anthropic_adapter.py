"""Unit tests for :mod:`lampgo.perception.anthropic_adapter`.

These tests are pure — no network, no httpx — so they run in milliseconds
and catch regressions in the translation layer without needing a real
Anthropic (or MiMo-Anthropic-compat) endpoint.

Coverage goals:

* ``translate_tools`` — drops non-function tools, renames ``parameters`` to
  ``input_schema``.
* ``translate_tool_choice`` — string and dict forms, all four values.
* ``split_system_and_messages`` — extracts system text, re-shapes assistant
  ``tool_calls`` into ``tool_use`` blocks (with ``json.loads`` of arguments),
  re-shapes tool results into ``tool_result`` blocks under a ``user`` role,
  translates ``image_url`` data URLs to ``image.source`` blocks, drops
  ``input_audio`` parts, joins multiple system messages.
* ``anthropic_response_to_openai_message`` — text + thinking + tool_use,
  ``stop_reason`` mapped to ``_finish_reason``.
* SSE parsing + accumulator — text deltas fire the content callback,
  thinking deltas fire the reasoning callback, tool_use ``input_json_delta``
  chunks concatenate into the final ``arguments`` string, ``stop_reason``
  is captured from ``message_delta``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from lampgo.perception import anthropic_adapter as A

# ---------------------------------------------------------------------------
# translate_tools
# ---------------------------------------------------------------------------


def test_translate_tools_renames_parameters_and_keeps_description() -> None:
    result = A.translate_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "set_expression",
                    "description": "Set the lamp's face.",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            }
        ]
    )
    assert result == [
        {
            "name": "set_expression",
            "description": "Set the lamp's face.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        }
    ]


def test_translate_tools_drops_non_function_tools() -> None:
    result = A.translate_tools(
        [
            {"type": "web_search", "web_search": {"max_keyword": 3}},
            {
                "type": "function",
                "function": {"name": "say", "parameters": {"type": "object"}},
            },
        ]
    )
    assert len(result) == 1
    assert result[0]["name"] == "say"


def test_translate_tools_empty_and_none() -> None:
    assert A.translate_tools(None) == []
    assert A.translate_tools([]) == []


# ---------------------------------------------------------------------------
# translate_tool_choice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "openai_choice,expected",
    [
        ("auto", {"type": "auto"}),
        ("required", {"type": "any"}),
        ("any", {"type": "any"}),
        ("none", {"type": "none"}),
        (
            {"type": "function", "function": {"name": "say"}},
            {"type": "tool", "name": "say"},
        ),
        (None, None),
    ],
)
def test_translate_tool_choice(openai_choice, expected) -> None:
    assert A.translate_tool_choice(openai_choice) == expected


def test_translate_tool_choice_unknown_string_defaults_to_auto() -> None:
    assert A.translate_tool_choice("weird") == {"type": "auto"}


# ---------------------------------------------------------------------------
# split_system_and_messages
# ---------------------------------------------------------------------------


def test_split_system_extracts_and_joins_multiple_system_messages() -> None:
    system, msgs = A.split_system_and_messages(
        [
            {"role": "system", "content": "You are a lamp."},
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "hi"},
        ]
    )
    assert system == "You are a lamp.\n\nBe concise."
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == [{"type": "text", "text": "hi"}]


def test_split_translates_assistant_tool_calls() -> None:
    _, msgs = A.split_system_and_messages(
        [
            {
                "role": "assistant",
                "content": "Turning the light on.",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "set_brightness",
                            "arguments": '{"level": 80}',
                        },
                    }
                ],
            }
        ]
    )
    assert msgs == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Turning the light on."},
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "set_brightness",
                    "input": {"level": 80},
                },
            ],
        }
    ]


def test_split_translates_tool_results_into_user_tool_result_blocks() -> None:
    _, msgs = A.split_system_and_messages(
        [
            {"role": "tool", "tool_call_id": "call_123", "content": '{"ok": true}'},
        ]
    )
    assert msgs == [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_123",
                    "content": '{"ok": true}',
                }
            ],
        }
    ]


def test_split_merges_parallel_tool_results_into_single_user_message() -> None:
    """Anthropic requires all tool_result blocks for a given assistant
    turn's parallel tool_uses to be in a SINGLE following user message.
    OpenAI emits one ``role: tool`` per tool_call; we must merge those."""
    _, msgs = A.split_system_and_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"r": 1}'},
            {"role": "tool", "tool_call_id": "c2", "content": '{"r": 2}'},
            {"role": "user", "content": "next turn"},
        ]
    )
    # Expected shape:
    #   assistant ← 2 tool_uses
    #   user      ← 2 tool_result blocks merged
    #   user      ← the real next-turn text
    assert [m["role"] for m in msgs] == ["assistant", "user", "user"]
    merged_tool_results = msgs[1]["content"]
    assert len(merged_tool_results) == 2
    assert [b["type"] for b in merged_tool_results] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in merged_tool_results] == ["c1", "c2"]
    # Trailing real user message must NOT have been swallowed into the merge.
    assert msgs[2]["content"] == [{"type": "text", "text": "next turn"}]


def test_split_translates_image_url_data_url_to_base64_source() -> None:
    data_url = "data:image/jpeg;base64,/9j/4AAQSkZ="
    _, msgs = A.split_system_and_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
    )
    assert msgs[0]["content"] == [
        {"type": "text", "text": "what is this?"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": "/9j/4AAQSkZ=",
            },
        },
    ]


def test_split_drops_input_audio_parts() -> None:
    _, msgs = A.split_system_and_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "listen:"},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "xxxx", "format": "wav"},
                    },
                ],
            }
        ]
    )
    # Only the text block survives; audio is silently dropped.
    assert msgs[0]["content"] == [{"type": "text", "text": "listen:"}]


def test_split_skips_vacuous_assistant_turn() -> None:
    """Assistant messages with no text and no tool_calls would 400 on
    Anthropic ('content: array too short').  We must drop them."""
    _, msgs = A.split_system_and_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "still here?"},
        ]
    )
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "user"]


def test_split_keeps_assistant_text_only_message() -> None:
    _, msgs = A.split_system_and_messages(
        [{"role": "assistant", "content": "Hello there."}]
    )
    assert msgs == [
        {"role": "assistant", "content": [{"type": "text", "text": "Hello there."}]}
    ]


def test_split_handles_malformed_tool_call_arguments() -> None:
    _, msgs = A.split_system_and_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "x", "arguments": "not json {{"},
                    }
                ],
            }
        ]
    )
    # Must not raise; input falls back to {}.
    tool_use = msgs[0]["content"][0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["input"] == {}


# ---------------------------------------------------------------------------
# build_request_body / headers
# ---------------------------------------------------------------------------


def test_build_request_body_shape() -> None:
    body = A.build_request_body(
        model="claude-3-5-sonnet-20241022",
        openai_messages=[
            {"role": "system", "content": "You are a lamp."},
            {"role": "user", "content": "hi"},
        ],
        openai_tools=[
            {
                "type": "function",
                "function": {"name": "say", "parameters": {"type": "object"}},
            }
        ],
        openai_tool_choice="required",
        max_tokens=1024,
        temperature=0.3,
        stream=True,
    )
    assert body["model"] == "claude-3-5-sonnet-20241022"
    assert body["max_tokens"] == 1024
    assert body["system"] == "You are a lamp."
    assert body["temperature"] == pytest.approx(0.3)
    assert body["stream"] is True
    assert body["tool_choice"] == {"type": "any"}
    assert body["tools"] == [
        {"name": "say", "description": "", "input_schema": {"type": "object"}}
    ]
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]


def test_build_request_body_omits_optional_fields_when_empty() -> None:
    body = A.build_request_body(
        model="m",
        openai_messages=[{"role": "user", "content": "hi"}],
        openai_tools=None,
        openai_tool_choice=None,
        max_tokens=16,
        temperature=0.0,
        stream=False,
    )
    assert "system" not in body
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "stream" not in body


def test_build_request_headers_sends_all_three_auth_styles() -> None:
    """Triple-auth: real Anthropic uses ``x-api-key``; MiMo's own curl uses
    ``api-key``; third-party Anthropic-compat proxies (including some MiMo
    SDK wrappers) use ``Authorization: Bearer``.  Sending all three keeps
    a single config working across providers without a mode switch."""
    h = A.build_request_headers("sk-test-123")
    assert h["x-api-key"] == "sk-test-123"
    assert h["api-key"] == "sk-test-123"
    assert h["Authorization"] == "Bearer sk-test-123"
    assert h["anthropic-version"] == A.ANTHROPIC_VERSION
    assert h["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# anthropic_response_to_openai_message
# ---------------------------------------------------------------------------


def test_response_translates_text_blocks_and_tool_use() -> None:
    resp = {
        "content": [
            {"type": "text", "text": "Sure, turning on. "},
            {"type": "text", "text": "Hold on."},
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "set_brightness",
                "input": {"level": 80},
            },
        ],
        "stop_reason": "tool_use",
    }
    msg = A.anthropic_response_to_openai_message(resp)
    assert msg["content"] == "Sure, turning on. Hold on."
    assert msg["_finish_reason"] == "tool_calls"
    assert msg["tool_calls"] == [
        {
            "id": "toolu_01",
            "type": "function",
            "function": {
                "name": "set_brightness",
                "arguments": json.dumps({"level": 80}, ensure_ascii=False),
            },
        }
    ]


def test_response_surfaces_thinking_as_reasoning_content() -> None:
    resp = {
        "content": [
            {"type": "thinking", "thinking": "Let me think..."},
            {"type": "text", "text": "Answer: 42"},
        ],
        "stop_reason": "end_turn",
    }
    msg = A.anthropic_response_to_openai_message(resp)
    assert msg["reasoning_content"] == "Let me think..."
    assert msg["content"] == "Answer: 42"
    assert msg["_finish_reason"] == "stop"


def test_response_maps_max_tokens_to_length() -> None:
    msg = A.anthropic_response_to_openai_message(
        {"content": [{"type": "text", "text": "..."}], "stop_reason": "max_tokens"}
    )
    assert msg["_finish_reason"] == "length"


# ---------------------------------------------------------------------------
# SSE parsing + accumulator
# ---------------------------------------------------------------------------


def _mk_sse_lines(events: list[tuple[str, dict]]) -> list[str]:
    """Format ``(event, data)`` pairs as the raw SSE lines httpx would yield."""
    out: list[str] = []
    for ev, data in events:
        out.append(f"event: {ev}")
        out.append(f"data: {json.dumps(data)}")
        out.append("")  # blank line = dispatch
    return out


async def _async_iter(items: list[str]):
    for item in items:
        yield item


def test_sse_accumulator_text_stream_fires_content_callback_and_finalises() -> None:
    async def run() -> dict:
        received: list[str] = []

        async def on_content(chunk: str) -> None:
            received.append(chunk)

        acc = A.AnthropicStreamAccumulator(on_content_delta=on_content)
        events = [
            ("message_start", {"message": {"id": "msg_01"}}),
            ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hello"}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": " world"}}),
            ("content_block_stop", {"index": 0}),
            ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {}}),
            ("message_stop", {}),
        ]
        async for ev in A.iter_sse_events(_async_iter(_mk_sse_lines(events))):
            await acc.consume_event(ev.event, ev.data)
        return acc.finalize(), received  # type: ignore[return-value]

    msg, received = asyncio.run(run())
    assert msg["content"] == "Hello world"
    assert msg.get("_finish_reason") == "stop"
    assert received == ["Hello", " world"]


def test_sse_accumulator_tool_use_stream_concatenates_input_json_delta() -> None:
    async def run() -> dict:
        acc = A.AnthropicStreamAccumulator()
        events = [
            ("message_start", {"message": {}}),
            (
                "content_block_start",
                {
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_42",
                        "name": "set_brightness",
                        "input": {},
                    },
                },
            ),
            (
                "content_block_delta",
                {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"lev'}},
            ),
            (
                "content_block_delta",
                {"index": 0, "delta": {"type": "input_json_delta", "partial_json": 'el": 80}'}},
            ),
            ("content_block_stop", {"index": 0}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {}}),
        ]
        async for ev in A.iter_sse_events(_async_iter(_mk_sse_lines(events))):
            await acc.consume_event(ev.event, ev.data)
        return acc.finalize()

    msg = asyncio.run(run())
    assert msg["content"] == ""
    assert msg["_finish_reason"] == "tool_calls"
    assert msg["tool_calls"] == [
        {
            "id": "toolu_42",
            "type": "function",
            "function": {"name": "set_brightness", "arguments": '{"level": 80}'},
        }
    ]


def test_sse_accumulator_thinking_delta_fires_reasoning_callback() -> None:
    async def run() -> dict:
        got: list[str] = []

        async def on_reasoning(chunk: str) -> None:
            got.append(chunk)

        acc = A.AnthropicStreamAccumulator(on_reasoning_delta=on_reasoning)
        events = [
            ("content_block_start", {"index": 0, "content_block": {"type": "thinking"}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "Hmm..."}}),
            ("content_block_stop", {"index": 0}),
        ]
        async for ev in A.iter_sse_events(_async_iter(_mk_sse_lines(events))):
            await acc.consume_event(ev.event, ev.data)
        return acc.finalize(), got  # type: ignore[return-value]

    msg, got = asyncio.run(run())
    assert got == ["Hmm..."]
    assert msg.get("reasoning_content") == "Hmm..."


def test_sse_accumulator_error_event_sets_error() -> None:
    async def run() -> str | None:
        acc = A.AnthropicStreamAccumulator()
        events = [
            (
                "error",
                {"error": {"type": "overloaded_error", "message": "too busy"}},
            ),
        ]
        async for ev in A.iter_sse_events(_async_iter(_mk_sse_lines(events))):
            await acc.consume_event(ev.event, ev.data)
        return acc.error

    assert asyncio.run(run()) == "overloaded_error: too busy"
