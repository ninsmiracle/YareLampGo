"""Capture scheduling and recovery regressions, without network or hardware."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from lampgo.core.config import CameraConfig, LLMConfig, MotionConfig, SafetyConfig
from lampgo.core.hal import MotorStartupState
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.types import JointState, MotionTarget
from lampgo.perception.camera import CameraCapture
from lampgo.perception.llm_client import LLMClient


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_tool", [False, True])
async def test_agent_camera_wait_keeps_event_loop_responsive(monkeypatch, capture_tool):
    client = LLMClient(
        LLMConfig(api_key="test"), [], camera_config=CameraConfig(port="" if capture_tool else "0"),
    )
    entered, release = threading.Event(), threading.Event()
    image = "data:image/jpeg;base64,dGVzdA=="
    capture_observed_event_loop = []

    def capture():
        entered.set()
        capture_observed_event_loop.append(release.wait(2))
        return image

    async def model(**kwargs):
        if capture_tool and not entered.is_set():
            client._camera._config.port = "0"
            return {"tool_calls": [{
                "id": "snapshot", "type": "function",
                "function": {"name": "capture_image", "arguments": "{}"},
            }]}
        assert any(
            part.get("image_url", {}).get("url") == image
            for msg in kwargs["messages"] if isinstance(msg.get("content"), list)
            for part in msg["content"]
        )
        return {"content": "看到了", "tool_calls": []}

    async def execute(*args):
        raise AssertionError("unexpected motion")

    monkeypatch.setattr(client._camera, "capture_data_url", capture)
    monkeypatch.setattr(client, "_stream_chat_completion", model)
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (None, None))
    task = asyncio.create_task(client.run_agent_loop("看看我", execute))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # This coroutine must run before the synchronous capture returns.
        release.set()
        await asyncio.wait_for(task, 3)
        assert capture_observed_event_loop == [True]
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_capture_cannot_queue_another_device_request(monkeypatch):
    camera = CameraCapture(CameraConfig(port="0"))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def capture():
        calls.append(1)
        entered.set()
        release.wait(2)
        return "image"

    monkeypatch.setattr(camera, "_capture_data_url", capture)
    task = asyncio.create_task(asyncio.to_thread(camera.capture_data_url))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(camera.capture_data_url) is None
        assert len(calls) == 1
    finally:
        release.set()
        # Join the real worker via the lock, not just the cancelled asyncio task.
        await asyncio.to_thread(camera._capture_lock.acquire)
        camera._capture_lock.release()
    assert await asyncio.to_thread(camera.capture_data_url) == "image"


def test_reconnect_recovery_joins_old_motion_loop_before_preflight():
    class Hal:
        motor_names = ["base_pitch"]
        startup_state = MotorStartupState.READY
        recovery_required = False
        is_connected = True

        def read_positions(self):
            return JointState(positions={"base_pitch": 26.0})

        def write_positions(self, positions, move_time_ms=0):
            pass

        def write_recovery_positions(self, positions):
            assert positions["base_pitch"] == 26.0

        def read_recovery_start(self):
            assert not old_thread.is_alive()
            return {"base_pitch": 26.0}

        def prepare_recovery(self, frames):
            assert not old_thread.is_alive()
            self.recovery_required = False
            self.startup_state = MotorStartupState.RECOVERING
            return {"base_pitch": 26.0}

    hal = Hal()
    motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig(breathing_enabled=False))
    motion.start()
    old_thread = motion._thread
    try:
        assert motion.ready_for_motion
        hal.recovery_required = True
        hal.startup_state = MotorStartupState.RECOVERY_REQUIRED
        assert not motion.ready_for_motion
        motion._current_target = MotionTarget(joints={"base_pitch": 60.0})
        frames = motion.prepare_recovery({"base_pitch": 26.0}, max_velocity=30.0)
        assert frames
        assert motion._current_target is None
        assert not old_thread.is_alive()
        assert motion.is_running
        assert motion._thread is not old_thread
        assert hal.startup_state == MotorStartupState.RECOVERING
    finally:
        motion.stop()


def test_recovery_refuses_to_start_if_old_thread_cannot_stop():
    hal = SimpleNamespace(recovery_required=True)
    motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig())
    motion._thread = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="did not stop"):
        motion.prepare_recovery({"base_pitch": 26.0}, max_velocity=30.0)


def test_motion_cannot_restart_while_a_previous_thread_is_stopping():
    motion = MotionRuntime(SimpleNamespace(), SafetyKernel(SafetyConfig()), MotionConfig())
    motion._thread = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="still stopping"):
        motion.start()
