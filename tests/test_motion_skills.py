"""Built-in motion skill tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_return_safe_disables_anticipation():
    from lampgo.core.types import MotionStatus
    from lampgo.skills.builtin.motion_skills import ReturnSafeSkill, set_calibration_home

    class FakeMotion:
        def __init__(self) -> None:
            self.target = None
            self.current_state = SimpleNamespace(get=lambda joint, default=0.0: default)
            self.status = MotionStatus()

        def move_to(self, target):
            import threading

            self.target = target
            done = threading.Event()
            done.set()
            return done

    async def run() -> None:
        motion = FakeMotion()
        set_calibration_home({"base_pitch": -25.4, "elbow_pitch": -25.3})
        result = await ReturnSafeSkill().execute(SimpleNamespace(motion=motion), velocity=60.0)

        assert result.status == "ok"
        assert motion.target.anticipation is False
        assert motion.target.joints["elbow_pitch"] == -25.3

    asyncio.run(run())
