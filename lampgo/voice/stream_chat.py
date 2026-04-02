"""Streaming LLM chat for voice pipeline — sentence-level token accumulation.

Uses SSE (stream=true) to receive tokens incrementally and yields
complete sentences as soon as they are detected, enabling pipelined
TTS synthesis while the LLM is still generating.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

SENTENCE_ENDS = re.compile(r"[。！？.!?\n]")

VOICE_SYSTEM_PROMPT = (
    "你是 lampgo，一个友好的智能台灯机器人。"
    "用简洁自然的口语回答，像朋友聊天一样。"
    "不要使用 Markdown 格式、代码块或列表。"
    "每次回复控制在 2-3 句话以内。"
)


async def stream_chat_sentences(
    text: str,
    api_key: str,
    api_base: str,
    model: str = "mimo-v2-pro",
    temperature: float = 0.7,
    max_tokens: int = 256,
    system_prompt: str = VOICE_SYSTEM_PROMPT,
) -> AsyncIterator[str]:
    """Send a chat request with stream=true and yield complete sentences.

    Each yielded string is a sentence suitable for immediate TTS synthesis.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("stream_chat.no_httpx")
        return

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "stream": True,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "api-key": api_key,
        "Content-Type": "application/json",
    }

    url = f"{api_base.rstrip('/')}/chat/completions"
    buffer = ""

    # mimo-v2-pro is a deep-thinking model — the first token can take 60s+.
    # Use a long read timeout but short connect timeout.
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info("stream_chat.requesting", model=model, text=text[:50])
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                first_token = True
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    delta = _extract_delta(chunk)
                    if not delta:
                        continue

                    if first_token:
                        logger.info("stream_chat.first_token", token=delta)
                        first_token = False

                    buffer += delta

                    while True:
                        m = SENTENCE_ENDS.search(buffer)
                        if m is None:
                            break
                        sentence = buffer[: m.end()].strip()
                        buffer = buffer[m.end():]
                        if sentence:
                            yield sentence

        if buffer.strip():
            yield buffer.strip()

    except Exception:
        logger.exception("stream_chat.request_failed")
        if buffer.strip():
            yield buffer.strip()


def _extract_delta(chunk: dict) -> str:
    """Extract text content delta from an SSE chunk."""
    choices = chunk.get("choices", [])
    if not choices:
        return ""
    delta = choices[0].get("delta", {})
    return delta.get("content") or ""
