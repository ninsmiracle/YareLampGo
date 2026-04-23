"""Anthropic ``/v1/messages`` adapter.

Why this exists
---------------

The rest of ``LLMClient`` is built around OpenAI's ``chat.completions`` shape:
a flat ``messages`` array that may contain a ``role=system`` entry at the top,
tools declared as ``{type:"function", function:{name, description, parameters}}``,
tool results re-entered as ``role=tool`` messages, images embedded as
``image_url`` (including ``data:`` URLs), and response ``tool_calls`` with
``arguments`` already serialized to a JSON string.

Anthropic's Messages API uses a different envelope for every one of those
concepts.  Rather than scattering ``if message_type == "anthropic"`` branches
through the request/response code, this module centralises all the
translation in one place:

* OpenAI → Anthropic   — for the request path (tools, tool_choice, messages).
* Anthropic → OpenAI   — for the non-streaming response (content blocks to
  a flat content + tool_calls with JSON-string arguments, matching what the
  agent loop already expects).
* :class:`AnthropicStreamAccumulator` — incrementally consumes SSE events and
  produces the same OpenAI-shaped final message + fires the existing
  ``on_reasoning_delta`` / ``on_content_delta`` callbacks.

Every function is pure and side-effect free except the accumulator, which
exists because SSE state must span many event lines.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"


# ---------------------------------------------------------------------------
# Request translation (OpenAI shape -> Anthropic shape)
# ---------------------------------------------------------------------------


def translate_tools(openai_tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Translate OpenAI function tools to Anthropic's flat tool spec.

    OpenAI:  ``{"type": "function", "function": {"name", "description",
                "parameters": <JSON schema>}}``
    Anthropic: ``{"name", "description", "input_schema": <JSON schema>}``

    Non-function tools (e.g. MiMo's private ``type=web_search``) are dropped
    with a warning — Anthropic's endpoint wouldn't understand them anyway.
    """
    if not openai_tools:
        return []
    out: list[dict[str, Any]] = []
    for tool in openai_tools:
        ttype = tool.get("type")
        if ttype == "function":
            fn = tool.get("function") or {}
            name = fn.get("name", "")
            if not name:
                continue
            params = fn.get("parameters") or {"type": "object", "properties": {}}
            out.append(
                {
                    "name": name,
                    "description": fn.get("description", ""),
                    "input_schema": params,
                }
            )
        else:
            logger.warning(
                "anthropic_adapter.tool_dropped",
                reason="non_function_tool_not_supported_on_anthropic",
                tool_type=ttype,
            )
    return out


def translate_tool_choice(choice: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate OpenAI ``tool_choice`` to Anthropic.

    OpenAI values:
        * ``"auto"``     — model decides
        * ``"required"`` — must call at least one tool
        * ``"none"``     — no tool calls allowed
        * ``{"type": "function", "function": {"name": "..."}}`` — force a tool

    Anthropic values:
        * ``{"type": "auto"}``
        * ``{"type": "any"}``    (equivalent of "required")
        * ``{"type": "none"}``
        * ``{"type": "tool", "name": "..."}``

    Returns ``None`` when input is ``None`` so the caller can omit the field.
    """
    if choice is None:
        return None
    if isinstance(choice, str):
        mapping = {
            "auto": {"type": "auto"},
            "required": {"type": "any"},
            "any": {"type": "any"},
            "none": {"type": "none"},
        }
        return mapping.get(choice, {"type": "auto"})
    if isinstance(choice, dict):
        if choice.get("type") == "function":
            fn = choice.get("function") or {}
            name = fn.get("name")
            if name:
                return {"type": "tool", "name": name}
        # already anthropic-shaped? pass through for forward-compat
        if choice.get("type") in ("auto", "any", "none", "tool"):
            return choice
    return {"type": "auto"}


_DATA_URL_RE = re.compile(r"^data:(?P<media>[^;,]+)(?:;base64)?,(?P<data>.+)$", re.DOTALL)


def _image_url_to_anthropic_source(url: str) -> dict[str, Any] | None:
    """Convert an OpenAI-style ``image_url`` into Anthropic's ``source`` dict.

    Only ``data:`` URLs with base64 payload are supported.  Public HTTPS
    URLs could be translated to ``{"type": "url", "url": ...}`` but the
    lamp's camera pipeline only ever emits data URLs, so we keep the
    surface minimal and log if something unexpected shows up.
    """
    if not isinstance(url, str) or not url:
        return None
    m = _DATA_URL_RE.match(url)
    if not m:
        if url.startswith("http://") or url.startswith("https://"):
            return {"type": "url", "url": url}
        logger.warning("anthropic_adapter.image_url_unrecognised", prefix=url[:32])
        return None
    media = (m.group("media") or "image/jpeg").strip() or "image/jpeg"
    data = (m.group("data") or "").strip()
    # Best-effort sanity: confirm it's base64 (if it's not, Anthropic would
    # reject the whole turn anyway; logging early helps debugging).
    try:
        base64.b64decode(data[:64] + "==", validate=False)
    except Exception:
        logger.warning("anthropic_adapter.image_base64_suspect", media=media)
    return {"type": "base64", "media_type": media, "data": data}


def _translate_parts(openai_content: Any) -> list[dict[str, Any]]:
    """Translate one OpenAI content value (string or list of parts) into
    Anthropic content blocks.

    Parts we know about:
        * ``{"type": "text", "text": ...}``  → ``{"type": "text", "text": ...}``
        * ``{"type": "image_url", "image_url": {"url": ...}}``
              → ``{"type": "image", "source": {...}}`` if convertible, else dropped.
        * ``{"type": "input_audio", ...}`` → **dropped** with a warning.  The
          Anthropic API doesn't accept audio input; upstream code that wants
          transcription must already have ingested the audio via a MiMo omni
          call before we hit this path.
    """
    if openai_content is None:
        return []
    if isinstance(openai_content, str):
        text = openai_content
        return [{"type": "text", "text": text}] if text else []
    if not isinstance(openai_content, list):
        # Unknown shape — coerce to string so the model still sees something.
        return [{"type": "text", "text": str(openai_content)}]

    out: list[dict[str, Any]] = []
    for part in openai_content:
        if not isinstance(part, dict):
            out.append({"type": "text", "text": str(part)})
            continue
        ptype = part.get("type")
        if ptype == "text":
            txt = part.get("text", "")
            if txt:
                out.append({"type": "text", "text": txt})
        elif ptype == "image_url":
            iu = part.get("image_url") or {}
            url = iu.get("url") if isinstance(iu, dict) else iu
            source = _image_url_to_anthropic_source(url or "")
            if source:
                out.append({"type": "image", "source": source})
        elif ptype == "input_audio":
            logger.warning(
                "anthropic_adapter.audio_dropped",
                reason="anthropic_messages_api_does_not_accept_audio_input",
            )
        elif ptype == "image":
            # Already anthropic-shaped (forward-compat).
            out.append(part)
        else:
            # Unknown part type — fall back to serialising as text so the
            # model at least sees *something* and we don't crash the request.
            logger.warning("anthropic_adapter.unknown_content_part", ptype=ptype)
            out.append({"type": "text", "text": json.dumps(part, ensure_ascii=False)})
    return out


def _tool_result_content_to_anthropic(content: Any) -> str | list[dict[str, Any]]:
    """Normalise an OpenAI tool result's ``content`` for Anthropic.

    Anthropic accepts either a plain string or a list of text/image blocks
    under ``tool_result.content``.  OpenAI callers almost always hand us a
    JSON string (the ``_run_agent_loop`` above uses ``json.dumps(tool_result)``),
    so the fast path is "pass the string through unchanged".  Mixed lists
    are translated via :func:`_translate_parts`.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = _translate_parts(content)
        return blocks or ""
    return json.dumps(content, ensure_ascii=False)


def split_system_and_messages(
    openai_messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Pull out ``role=system`` entries into Anthropic's top-level ``system``
    string and translate the rest of the history.

    Also handles the tricky re-shaping of assistant tool calls and tool
    results:

    * ``{"role": "assistant", "content": "...", "tool_calls": [ ... ]}``
      → ``{"role": "assistant", "content": [{"type":"text", ...},
                                              {"type":"tool_use", id, name, input}]}``
      where ``input`` must be a **dict**, so we ``json.loads`` the OpenAI
      ``arguments`` string (falling back to ``{}`` on malformed JSON).

    * ``{"role": "tool", "tool_call_id": ..., "content": ...}``
      → ``{"role": "user", "content": [{"type": "tool_result",
                                          "tool_use_id": ..., "content": ...}]}``

    Adjacent ``user`` / ``tool_result`` messages are **not** merged here;
    Anthropic accepts consecutive messages of the same role as long as each
    has valid content.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    for msg in openai_messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            elif isinstance(content, list):
                # Flatten system content parts to text (Anthropic's system is
                # a plain string, it doesn't accept multi-part blocks).
                text_bits = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                joined = "\n".join(b for b in text_bits if b)
                if joined:
                    system_parts.append(joined)
            continue

        if role == "tool":
            tool_use_id = msg.get("tool_call_id", "")
            tr_content = _tool_result_content_to_anthropic(content)
            tr_block = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": tr_content,
            }
            # Anthropic requires that **all** ``tool_result`` blocks for a
            # single assistant turn's tool_uses live in **one** following
            # user message.  The OpenAI wire format instead uses one
            # ``role: tool`` message per tool_call, so a parallel 2-tool
            # turn becomes two consecutive `role: tool` entries.  If we
            # naively translate each into its own user message, Anthropic
            # 400's with "messages: tool_use ids were found without
            # tool_result blocks immediately after".
            #
            # Fix: merge into the trailing user message if that message is
            # already nothing-but-tool_result blocks.
            if (
                out
                and out[-1].get("role") == "user"
                and isinstance(out[-1].get("content"), list)
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in out[-1]["content"]
                )
            ):
                out[-1]["content"].append(tr_block)
            else:
                out.append({"role": "user", "content": [tr_block]})
            continue

        if role == "assistant":
            blocks = _translate_parts(content) if content else []
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "") or ""
                try:
                    input_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    if not isinstance(input_dict, dict):
                        input_dict = {}
                except json.JSONDecodeError:
                    input_dict = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": name,
                        "input": input_dict,
                    }
                )
            # Assistant messages MUST have at least one block on Anthropic.
            # An empty ``content`` combined with zero tool_calls means the
            # assistant turn is vacuous; skip it so we don't 400 the request.
            if not blocks:
                continue
            out.append({"role": "assistant", "content": blocks})
            continue

        if role == "user":
            blocks = _translate_parts(content)
            if not blocks:
                continue
            out.append({"role": "user", "content": blocks})
            continue

        # Unknown role — safest to drop with a log rather than 400 the API.
        logger.warning("anthropic_adapter.unknown_role", role=role)

    return "\n\n".join(p for p in system_parts if p), out


def build_request_body(
    *,
    model: str,
    openai_messages: list[dict[str, Any]],
    openai_tools: list[dict[str, Any]] | None,
    openai_tool_choice: str | dict[str, Any] | None,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> dict[str, Any]:
    """Assemble a ready-to-POST Anthropic ``/v1/messages`` body from
    OpenAI-shaped inputs.  ``max_tokens`` is **required** by Anthropic; the
    caller is expected to have picked a sensible value.
    """
    system, messages = split_system_and_messages(openai_messages)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max(1, int(max_tokens)),
        "messages": messages,
        "temperature": float(temperature),
    }
    if system:
        body["system"] = system
    tools = translate_tools(openai_tools)
    if tools:
        body["tools"] = tools
    tc = translate_tool_choice(openai_tool_choice)
    if tc is not None:
        body["tool_choice"] = tc
    if stream:
        body["stream"] = True
    return body


def build_request_headers(api_key: str) -> dict[str, str]:
    """Headers for Anthropic-style ``/v1/messages`` calls.

    We intentionally send **three** auth headers at once because popular
    Anthropic-compatible endpoints disagree on which one to check:

    * Real Anthropic (``api.anthropic.com``) reads ``x-api-key``.
    * MiMo's own curl examples use ``api-key`` (no ``x-`` prefix).
    * Some Anthropic-compatible examples quote
      ``Authorization: Bearer``, and OpenRouter-style proxies also accept
      Bearer.

    Sending all three lets a single config work across real Anthropic,
    MiMo's ``/anthropic/v1`` endpoint, and generic Anthropic-compat proxies
    without the user having to guess which auth style the upstream wants.
    Each server will simply pick up the header it recognises and ignore
    the rest.  ``anthropic-version`` is harmless on proxies that don't use it.
    """
    return {
        "x-api-key": api_key,
        "api-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# Response translation (Anthropic shape -> OpenAI shape expected upstream)
# ---------------------------------------------------------------------------


def _finish_reason_to_openai(stop_reason: str | None) -> str:
    """Map Anthropic ``stop_reason`` to the closest OpenAI ``finish_reason``.

    This only matters for logging / ``finish_reason == "length"`` truncation
    detection; upstream code doesn't branch on anything else.
    """
    if not stop_reason:
        return ""
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "stop",
    }.get(stop_reason, stop_reason)


def anthropic_response_to_openai_message(resp_json: dict[str, Any]) -> dict[str, Any]:
    """Turn a non-streaming Anthropic response into the message dict that the
    agent loop already knows how to consume.

    The returned dict has the same shape as what our OpenAI-path
    ``_stream_chat_completion`` produces at the end of a stream:

    * ``content`` — concatenated text blocks (may be empty string).
    * ``tool_calls`` — list of
        ``{"id", "type":"function", "function":{"name", "arguments": <json str>}}``
      — only present if the model emitted at least one ``tool_use`` block.
    * ``reasoning_content`` — concatenated text from any ``thinking`` blocks,
      to stay compatible with how the agent loop surfaces chain-of-thought.
    * ``_finish_reason`` (debug) — mapped stop reason, for log hygiene.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in resp_json.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "") or ""
            if text:
                content_parts.append(text)
        elif btype == "thinking":
            thinking = block.get("thinking", "") or ""
            if thinking:
                reasoning_parts.append(thinking)
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(
                            block.get("input") or {}, ensure_ascii=False
                        ),
                    },
                }
            )
        # Unknown block types (e.g. future ``server_tool_use``) are silently
        # ignored here; the model still got its tool calls / text to us.

    out: dict[str, Any] = {"content": "".join(content_parts)}
    if reasoning_parts:
        out["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        out["tool_calls"] = tool_calls
    stop = resp_json.get("stop_reason")
    if stop:
        out["_finish_reason"] = _finish_reason_to_openai(stop)
    return out


# ---------------------------------------------------------------------------
# Streaming (SSE) accumulator
# ---------------------------------------------------------------------------


@dataclass
class _ToolUseInProgress:
    """Per-block state while we accumulate a ``tool_use`` from SSE deltas.

    Arguments arrive as a stream of ``input_json_delta`` partial-JSON chunks
    that we concatenate verbatim (Anthropic guarantees they form valid JSON
    when joined).  We don't try to parse mid-stream — the final JSON string
    is what the agent loop wants anyway (it will ``json.loads`` itself).
    """

    block_id: str = ""
    name: str = ""
    partial_args: list[str] = field(default_factory=list)

    def to_openai_tool_call(self) -> dict[str, Any]:
        return {
            "id": self.block_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": "".join(self.partial_args),
            },
        }


class AnthropicStreamAccumulator:
    """State machine that turns Anthropic SSE events into our OpenAI-shaped
    final message and pushes token-level deltas to the same callbacks the
    OpenAI-path streaming code uses.

    Anthropic SSE event types we care about:

    * ``message_start``          — begins a new message. Mostly metadata.
    * ``content_block_start``    — a new block begins at ``index``. Block
                                    type is one of ``text``, ``thinking``,
                                    ``tool_use``.
    * ``content_block_delta``    — incremental update to the block at
                                    ``index``.  Delta shapes:
        - ``{"type": "text_delta", "text": "..."}``
        - ``{"type": "thinking_delta", "thinking": "..."}``
        - ``{"type": "input_json_delta", "partial_json": "..."}``  (tool_use)
    * ``content_block_stop``     — block at ``index`` is done.
    * ``message_delta``          — updates to top-level message (stop_reason).
    * ``message_stop``           — message is fully complete.
    * ``ping``                   — keepalive; ignore.
    * ``error``                  — fatal; we surface via :meth:`error`.

    Unknown events are ignored so future-proofing is a no-op.
    """

    def __init__(
        self,
        *,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._on_reasoning = on_reasoning_delta
        self._on_content = on_content_delta
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_uses: dict[int, _ToolUseInProgress] = {}
        self._block_types: dict[int, str] = {}
        self._stop_reason: str | None = None
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    async def consume_event(self, event_name: str, data: dict[str, Any]) -> None:
        """Feed one decoded SSE event into the state machine.

        ``data`` is the already-JSON-decoded payload of the event's ``data:``
        line.  Events with unparseable payloads should be dropped before
        reaching here.
        """
        if event_name == "content_block_start":
            idx = int(data.get("index", 0))
            block = data.get("content_block") or {}
            btype = block.get("type", "")
            self._block_types[idx] = btype
            if btype == "tool_use":
                self._tool_uses[idx] = _ToolUseInProgress(
                    block_id=block.get("id") or "",
                    name=block.get("name") or "",
                )
            return

        if event_name == "content_block_delta":
            idx = int(data.get("index", 0))
            delta = data.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                chunk = delta.get("text") or ""
                if chunk:
                    self._content_parts.append(chunk)
                    if self._on_content:
                        await self._on_content(chunk)
            elif dtype == "thinking_delta":
                chunk = delta.get("thinking") or ""
                if chunk:
                    self._reasoning_parts.append(chunk)
                    if self._on_reasoning:
                        await self._on_reasoning(chunk)
            elif dtype == "input_json_delta":
                chunk = delta.get("partial_json") or ""
                if chunk and idx in self._tool_uses:
                    self._tool_uses[idx].partial_args.append(chunk)
            # Unknown delta types are ignored — safer than guessing.
            return

        if event_name == "content_block_stop":
            return  # nothing to do; state already captured in deltas

        if event_name == "message_delta":
            delta = data.get("delta") or {}
            sr = delta.get("stop_reason")
            if sr:
                self._stop_reason = sr
            return

        if event_name == "message_start":
            # Anthropic can report ``stop_reason`` at message_start too in
            # edge cases (e.g. cached completions).  Pick it up if present.
            msg = data.get("message") or {}
            sr = msg.get("stop_reason")
            if sr:
                self._stop_reason = sr
            return

        if event_name == "error":
            err = data.get("error") or {}
            etype = err.get("type", "error")
            emsg = err.get("message", "")
            self._error = f"{etype}: {emsg}" if emsg else etype
            return

        # ``ping``, ``message_stop``, and any future events: nothing to do.

    def finalize(self) -> dict[str, Any]:
        """Return the OpenAI-shaped message dict produced by the stream.

        Matches the shape of ``_stream_chat_completion``'s return value so
        the agent loop can keep reading ``content`` / ``tool_calls`` /
        ``reasoning_content`` without caring which provider answered.
        """
        message: dict[str, Any] = {"content": "".join(self._content_parts)}
        if self._reasoning_parts:
            message["reasoning_content"] = "".join(self._reasoning_parts)
        if self._tool_uses:
            message["tool_calls"] = [
                self._tool_uses[i].to_openai_tool_call()
                for i in sorted(self._tool_uses)
            ]
        if self._stop_reason:
            message["_finish_reason"] = _finish_reason_to_openai(self._stop_reason)
        return message


# ---------------------------------------------------------------------------
# Raw SSE line parser
# ---------------------------------------------------------------------------


@dataclass
class _SseEvent:
    event: str
    data: dict[str, Any]


async def iter_sse_events(line_iter: Any) -> Any:
    """Async-generate :class:`_SseEvent` from an async iterator of raw SSE
    lines (as produced by ``httpx.AsyncClient.stream().aiter_lines()``).

    SSE format reminder::

        event: message_start
        data: { ... json ... }

        event: content_block_delta
        data: { ... }

    We keep state per-event so that multiline ``data:`` fields (rare from
    Anthropic but permitted by SSE spec) concatenate correctly.
    """
    event_name = ""
    data_buf: list[str] = []
    async for raw in line_iter:
        line = raw.rstrip("\r")
        if line == "":
            # blank line = dispatch
            if data_buf and event_name:
                payload = "\n".join(data_buf)
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(
                        "anthropic_adapter.sse_bad_json",
                        event=event_name,
                        preview=payload[:120],
                    )
                    event_name = ""
                    data_buf = []
                    continue
                yield _SseEvent(event=event_name, data=parsed)
            event_name = ""
            data_buf = []
            continue
        if line.startswith(":"):
            continue  # SSE comment
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_buf.append(line[len("data:") :].lstrip())
        # Other fields (``id:``, ``retry:``) are uninteresting here.

    # Flush any trailing event that wasn't terminated by a blank line.
    if data_buf and event_name:
        payload = "\n".join(data_buf)
        try:
            parsed = json.loads(payload)
            yield _SseEvent(event=event_name, data=parsed)
        except json.JSONDecodeError:
            pass
