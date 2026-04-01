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
class OpenClawTaskUpdated(Event):
    """OpenClaw task status changed."""

    request_id: str
    task: dict


@dataclass
class OpenClawPromotionRequested(Event):
    """OpenClaw task requires manual promotion confirmation."""

    request_id: str
    task_id: str
    proposal: dict
    task: dict


@dataclass
class OpenClawPromotionDecision(Event):
    """User approved or rejected a promotion proposal."""

    request_id: str
    task_id: str
    proposal_id: str
    decision: str
    task: dict


@dataclass
class OpenClawAskRequested(Event):
    """OpenClaw asked the user a question via lampgo."""

    ask_id: str
    question: str
    options: list[str]
    request_id: str = ""


@dataclass
class OpenClawAskResolved(Event):
    """User replied to an OpenClaw question via lampgo."""

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


class EventBus:
    """Simple in-process typed pub/sub.  Handlers are async callables."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[..., Awaitable[None]]]] = defaultdict(list)

    def subscribe(self, event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        handlers = self._handlers.get(type(event), [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("eventbus.handler_error", event_type=type(event).__name__)
