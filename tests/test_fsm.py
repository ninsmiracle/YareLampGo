"""Tests for the StateMachine."""

from lampgo.core.types import DeviceState
from lampgo.skills.fsm import StateMachine


def test_initial_state_is_idle():
    fsm = StateMachine()
    assert fsm.state == DeviceState.IDLE
    assert fsm.is_idle


def test_valid_transition():
    fsm = StateMachine()
    assert fsm.transition(DeviceState.EXECUTING)
    assert fsm.state == DeviceState.EXECUTING


def test_invalid_transition():
    fsm = StateMachine()
    assert not fsm.transition(DeviceState.RECOVERING)
    assert fsm.state == DeviceState.IDLE


def test_force_override():
    fsm = StateMachine()
    fsm.force(DeviceState.SAFE_STOP)
    assert fsm.is_safe_stopped


def test_full_lifecycle():
    fsm = StateMachine()
    assert fsm.transition(DeviceState.EXECUTING)
    assert fsm.transition(DeviceState.SAFE_STOP)
    assert fsm.transition(DeviceState.RECOVERING)
    assert fsm.transition(DeviceState.IDLE)
    assert fsm.is_idle
