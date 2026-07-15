"""Lightweight typed event bus — publish/subscribe within a single process."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound="Event")


@dataclass
class Event:
    """Base class for all events on the bus."""


@dataclass
class SkillStarted(Event):
    skill_id: str
    invocation_id: str


@dataclass
class SkillFinished(Event):
    skill_id: str
    invocation_id: str
    status: str


@dataclass
class SkillCancelled(Event):
    skill_id: str
    invocation_id: str


@dataclass
class SafetyTriggered(Event):
    reason: str
    joint: str | None = None


@dataclass
class EStopActivated(Event):
    reason: str


@dataclass
class EStopReset(Event):
    pass


@dataclass
class IntentRouting(Event):
    """Intent classification has started."""

    text: str
    request_id: str = ""


@dataclass
class IntentResolved(Event):
    """Intent classification completed."""

    intent_type: str
    skill_id: str | None = None
    chat_response: str | None = None
    source: str = ""
    detail: str | None = None
    matched_keyword: str | None = None
    request_id: str = ""


@dataclass
class IntentProgress(Event):
    """Intent classification progress update."""

    stage: str
    message: str
    source: str = ""
    request_id: str = ""


@dataclass
class ToolCallPlanned(Event):
    """LLM agent decided to call a tool."""

    request_id: str
    turn_index: int
    tool_index: int
    tool_name: str
    arguments: dict


@dataclass
class ToolCallFinished(Event):
    """LLM agent tool call completed."""

    request_id: str
    turn_index: int
    tool_index: int
    tool_name: str
    status: str
    invocation_id: str | None = None
    summary: str = ""
    error: str | None = None


@dataclass
class AgentFinished(Event):
    """LLM agent loop finished."""

    request_id: str
    stop_reason: str
    tool_call_count: int
    response: str | None = None
    detail: str | None = None


@dataclass
class AgentTaskUpdated(Event):
    """External agent task status changed."""

    request_id: str
    task: dict
    progress: dict | None = None


@dataclass
class AgentAskRequested(Event):
    """An external agent asked the user a question via LampGo."""

    ask_id: str
    question: str
    options: list[str]
    request_id: str = ""


@dataclass
class AgentAskResolved(Event):
    """User replied to an external agent question via LampGo."""

    ask_id: str
    reply: str
    request_id: str = ""


@dataclass
class SkillProgress(Event):
    """Skill execution progress update."""

    skill_id: str
    invocation_id: str
    progress: float
    message: str = ""


@dataclass
class ChatMessage(Event):
    """A message to display to the user."""

    role: str
    content: str
    request_id: str = ""


@dataclass
class TtsAudio(Event):
    """A chunk of TTS audio to play in the browser."""

    audio: str
    format: str = "mp3"
    sample_rate: int = 0
    request_id: str = ""


@dataclass
class ConversationStateChanged(Event):
    """Voice conversation state changed (idle / joining / active / leaving)."""

    state: str


@dataclass
class WakeWordDetected(Event):
    """Wake word detected by the server-side listener."""

    model: str = ""
    score: float = 0.0


@dataclass
class VoiceUserText(Event):
    """ASR text received from voice pipeline (Agent SDK)."""

    user_text: str
    request_id: str = ""


@dataclass
class Esp32AudioRelay(Event):
    """Raw ESP32 PCM chunk relayed to browser for LiveKit publishing."""

    pcm: bytes


class EventBus:
    """Simple in-process typed pub/sub.  Handlers are async callables."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[..., Awaitable[None]]]] = defaultdict(list)

    def subscribe(self, event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> None:
        try:
            self._handlers[event_type].remove(handler)
        except (KeyError, ValueError):
            pass

    async def publish(self, event: Event) -> None:
        handlers = self._handlers.get(type(event), [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("eventbus.handler_error", event_type=type(event).__name__)
