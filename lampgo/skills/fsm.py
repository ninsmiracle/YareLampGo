"""Simple finite state machine for device lifecycle.

M1 states: Idle, Executing, SafeStop, Recovering.
Teleop added in M3.
"""

from __future__ import annotations

import structlog

from lampgo.core.types import DeviceState

logger = structlog.get_logger(__name__)

_TRANSITIONS: dict[DeviceState, set[DeviceState]] = {
    DeviceState.IDLE: {DeviceState.EXECUTING, DeviceState.SAFE_STOP, DeviceState.TELEOP},
    DeviceState.EXECUTING: {DeviceState.IDLE, DeviceState.SAFE_STOP},
    DeviceState.SAFE_STOP: {DeviceState.RECOVERING},
    DeviceState.RECOVERING: {DeviceState.IDLE},
    DeviceState.TELEOP: {DeviceState.IDLE, DeviceState.SAFE_STOP},
}


class StateMachine:
    """Tracks the device's current operational state."""

    def __init__(self) -> None:
        self._state = DeviceState.IDLE

    @property
    def state(self) -> DeviceState:
        return self._state

    def transition(self, target: DeviceState) -> bool:
        """Attempt a state transition. Returns True on success."""
        allowed = _TRANSITIONS.get(self._state, set())
        if target not in allowed:
            logger.warning("fsm.invalid_transition", current=self._state.value, target=target.value)
            return False
        prev = self._state
        self._state = target
        logger.info("fsm.transition", prev=prev.value, new=target.value)
        return True

    def force(self, target: DeviceState) -> None:
        """Force a state (for safety overrides)."""
        prev = self._state
        self._state = target
        logger.warning("fsm.forced", prev=prev.value, new=target.value)

    @property
    def is_idle(self) -> bool:
        return self._state == DeviceState.IDLE

    @property
    def is_safe_stopped(self) -> bool:
        return self._state == DeviceState.SAFE_STOP
