"""Lightweight typed event bus — publish/subscribe within a single process."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

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
