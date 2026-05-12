"""OpenAI-compatible ``/v1/chat/completions`` endpoint.

Exposes lampgo's full LLM + persona + memory + tools pipeline as an
OpenAI-compatible streaming chat API so external consumers (e.g. the
Xiaomi LiveKit Agent SDK) can use lampgo as their LLM backend.

Wire format follows the OpenAI Chat Completions SSE spec:
  - Each chunk: ``data: {JSON}\n\n``
  - Final sentinel: ``data: [DONE]\n\n``

Preemption: when a new request arrives while a previous one is still
executing, the previous handle_request task is cancelled so only the
latest user utterance is actively processed.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from starlette.requests import Request
from starlette.responses import StreamingResponse

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

_SENTENCE_ENDINGS = "。！？.!?\n"
_TTS_FLUSH_MARKER = "\ue000LAMPGO_TTS_FLUSH\ue000"


def _make_chunk(
    chat_id: str,
    content: str | None = None,
    *,
    finish_reason: str | None = None,
    model: str = "lampgo",
) -> str:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if finish_reason is not None:
        delta["role"] = "assistant"

    payload = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _as_tts_sentence(text: str) -> str:
    """Ensure each narration chunk has a sentence boundary for LiveKit TTS.

    The Xiaomi LiveKit Agent SDK drives TTS from streamed LLM content using a
    sentence tokenizer.  Many of our ``say`` snippets end in ``~``/``～``,
    which sounds natural in text but is not always treated as a flush boundary.
    Appending a neutral Chinese period makes each ``say`` eligible for immediate
    TTS instead of being buffered until the whole response finishes.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    if cleaned[-1] in _SENTENCE_ENDINGS:
        return cleaned
    return f"{cleaned}。"


def _make_flush_chunk(chat_id: str, *, model: str = "lampgo") -> str:
    """Emit a private marker that the SDK-side sitecustomize patch converts
    into LiveKit's FlushSentinel.

    OpenAI Chat Completions has no standard "flush current TTS segment" event.
    LiveKit's internal voice pipeline does, so we tunnel a private-use Unicode
    marker through the text stream and strip it inside the SDK worker process.
    """
    return _make_chunk(chat_id, _TTS_FLUSH_MARKER, model=model)


async def handle_chat_completions(request: Request) -> StreamingResponse:
    """POST /v1/chat/completions — OpenAI-compatible streaming endpoint."""
    server: LampgoServer = request.app.state.lampgo_server

    try:
        body = await request.json()
    except Exception:
        return StreamingResponse(
            _error_stream("invalid JSON body"),
            media_type="text/event-stream",
            status_code=400,
        )

    messages: list[dict] = body.get("messages", [])
    user_text = _extract_user_text(messages)
    if not user_text:
        return StreamingResponse(
            _error_stream("no user message found"),
            media_type="text/event-stream",
            status_code=400,
        )

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    request_id = uuid.uuid4().hex[:12]

    logger.info(
        "llm_compat.request",
        chat_id=chat_id,
        request_id=request_id,
        user_text=user_text[:80],
        msg_count=len(messages),
        stream=body.get("stream", True),
    )

    async def _generate():
        from lampgo.core.events import IntentProgress, VoiceUserText

        # --- Preemption: cancel any in-flight generation ---
        prev_task: asyncio.Task | None = getattr(server, "_llm_active_task", None)
        prev_rid: str = getattr(server, "_llm_active_request_id", "")
        if prev_task is not None and not prev_task.done():
            logger.info("llm_compat.preempting", chat_id=chat_id, user_text=user_text[:40])
            prev_task.cancel()
            try:
                await asyncio.wait_for(prev_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            if prev_rid:
                await _broadcast_result(server, {
                    "ok": True,
                    "result": {"preempted": True, "response": ""},
                }, prev_rid)

        # --- Cancel any pending goodbye hangup ---
        # If the previous turn called end_conversation, _schedule_end_conversation
        # may still be waiting for the goodbye TTS to play out. A new user
        # utterance means the user wants to keep talking, so abort the planned
        # hangup before it disconnects us mid-conversation.
        hangup_task: asyncio.Task | None = getattr(server, "_pending_hangup_task", None)
        if hangup_task is not None and not hangup_task.done():
            hangup_rid: str = getattr(server, "_pending_hangup_request_id", "")
            logger.info(
                "llm_compat.cancelling_pending_hangup",
                chat_id=chat_id,
                hangup_request_id=hangup_rid,
                user_text=user_text[:40],
            )
            hangup_task.cancel()
            try:
                await asyncio.wait_for(hangup_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass

        wake_loop = getattr(server, "_wake_loop", None)
        bridge = getattr(wake_loop, "bridge", None)
        history = _extract_history(messages)
        consume_drop_history_once = getattr(bridge, "consume_drop_history_once", None)
        if callable(consume_drop_history_once) and consume_drop_history_once():
            logger.info(
                "llm_compat.drop_sdk_history_for_new_call",
                request_id=request_id,
                dropped_history_len=len(history),
            )
            history = []
        mark_user_voice_activity = getattr(bridge, "mark_user_voice_activity", None)
        if callable(mark_user_voice_activity):
            mark_user_voice_activity()

        await server.events.publish(VoiceUserText(user_text=user_text, request_id=request_id))

        text_queue: asyncio.Queue[str | None] = asyncio.Queue()
        role_sent = False
        emitted_texts: list[str] = []

        async def _yield_text(text: str):
            nonlocal role_sent
            tts_text = _as_tts_sentence(text)
            if not tts_text:
                return
            emitted_texts.append(tts_text)
            if not role_sent:
                yield _make_chunk(chat_id, "", model="lampgo")
                role_sent = True
            logger.debug(
                "llm_compat.stream_text",
                chat_id=chat_id,
                request_id=request_id,
                text=tts_text[:80],
            )
            for i in range(0, len(tts_text), 20):
                yield _make_chunk(chat_id, tts_text[i : i + 20], model="lampgo")
            yield _make_flush_chunk(chat_id, model="lampgo")

        async def _on_narration(event: IntentProgress) -> None:
            if event.request_id != request_id:
                return
            if event.stage == "llm_narration" and event.message.strip():
                await text_queue.put(event.message.strip())

        server.events.subscribe(IntentProgress, _on_narration)

        task = asyncio.create_task(server.handle_request({
            "cmd": "text",
            "input": user_text,
            "request_id": request_id,
            "history": history,
            "call_mode": True,
        }))
        server._llm_active_task = task  # type: ignore[attr-defined]
        server._llm_active_request_id = request_id  # type: ignore[attr-defined]

        try:
            while not task.done():
                try:
                    text = await asyncio.wait_for(text_queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                except asyncio.CancelledError:
                    break
                async for chunk in _yield_text(text):
                    yield chunk

            while not text_queue.empty():
                text = text_queue.get_nowait()
                async for chunk in _yield_text(text):
                    yield chunk

            if not task.cancelled():
                try:
                    result = task.result()
                    fallback = _extract_response(result)
                    fallback_sentence = _as_tts_sentence(fallback)
                    if fallback_sentence and fallback_sentence not in emitted_texts:
                        async for chunk in _yield_text(fallback_sentence):
                            yield chunk
                    await _broadcast_result(server, result, request_id)
                except (asyncio.CancelledError, Exception):
                    pass

            yield _make_chunk(chat_id, finish_reason="stop", model="lampgo")
            yield "data: [DONE]\n\n"
        except Exception:
            logger.exception("llm_compat.generation_error", chat_id=chat_id)
            if not task.done():
                task.cancel()
            yield _make_chunk(chat_id, "[internal error]", model="lampgo")
            yield _make_chunk(chat_id, finish_reason="stop", model="lampgo")
            yield "data: [DONE]\n\n"
        finally:
            server.events.unsubscribe(IntentProgress, _on_narration)
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _extract_user_text(messages: list[dict]) -> str:
    """Extract the text content from the last user message."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                return " ".join(parts).strip()
    return ""


def _msg_text(msg: dict) -> str:
    """Extract plain text from a message's content (string or content-parts)."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        return " ".join(parts).strip()
    return ""


def _extract_history(messages: list[dict]) -> list[dict[str, str]]:
    """Extract conversation history from OpenAI-format messages.

    Returns all user/assistant messages that appear *before* the last
    user message, suitable for passing as ``history`` to handle_request.
    """
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None or last_user_idx == 0:
        return []

    history: list[dict[str, str]] = []
    for msg in messages[:last_user_idx]:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _msg_text(msg)
        if text:
            history.append({"role": role, "content": text})
    return history


def _extract_response(result: dict) -> str:
    """Pull the final text response from handle_request's return dict."""
    r = result.get("result", {})
    return r.get("response") or r.get("chat_response") or ""


async def _broadcast_result(server: LampgoServer, result: dict, request_id: str) -> None:
    """Broadcast the handle_request result to frontend WS clients.

    The call view's finishPending needs this to display the response text.
    """
    gw = getattr(server, "_web_gateway", None)
    if gw is None:
        return
    bridge = getattr(gw, "bridge", None)
    if bridge is None:
        return
    msg = dict(result)
    msg["request_id"] = request_id
    try:
        await bridge.broadcast(msg)
    except Exception:
        logger.debug("llm_compat.broadcast_failed", request_id=request_id, exc_info=True)


async def _error_stream(msg: str):
    payload = {
        "error": {"message": msg, "type": "invalid_request_error"},
    }
    yield f"data: {json.dumps(payload)}\n\n"
    yield "data: [DONE]\n\n"
