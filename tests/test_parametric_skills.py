"""Regression tests for parametric factory motions."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace


def test_idle_sway_repeated_runs_share_calibration_centre_and_recenter(monkeypatch) -> None:
    from lampgo.core.types import JointState, MotionStatus
    from lampgo.skills.builtin import parametric_skills
    from lampgo.skills.builtin.motion_skills import set_calibration_home
    from lampgo.skills.builtin.parametric_skills import IdleSwaySkill

    monkeypatch.setattr(parametric_skills, "_jitter", lambda value, ratio=0.15: value)
    monkeypatch.setattr(parametric_skills.random, "choice", lambda values: values[-1])
    monkeypatch.setattr(parametric_skills.random, "uniform", lambda low, high: (low + high) / 2.0)

    centre = {"base_yaw": 1.3, "base_pitch": 27.3}
    set_calibration_home({**centre, "elbow_pitch": -0.9, "wrist_roll": 22.5, "wrist_pitch": -4.9})

    class FakeMotion:
        def __init__(self) -> None:
            self.streams: list[list[dict[str, float]]] = []
            self.targets = []
            self.current_state = JointState(positions=dict(centre))
            self.status = MotionStatus()

        def stream_frames(self, frames, fps=50):
            del fps
            self.streams.append([dict(frame) for frame in frames])
            done = threading.Event()
            done.set()
            return done

        def move_to(self, target):
            self.targets.append(target)
            self.current_state = JointState(positions=dict(target.joints))
            done = threading.Event()
            done.set()
            return done

        def stop_immediate(self):
            pass

    async def run() -> None:
        motion = FakeMotion()
        skill = IdleSwaySkill()

        first = await skill.execute(
            SimpleNamespace(
                motion=motion,
                state=JointState(positions={"base_pitch": 12.0, "base_yaw": -8.0}),
            ),
            amplitude=6.0,
            period=4.0,
            duration=1.0,
        )
        second = await skill.execute(
            SimpleNamespace(
                motion=motion,
                state=JointState(positions={"base_pitch": -5.0, "base_yaw": 15.0}),
            ),
            amplitude=6.0,
            period=4.0,
            duration=1.0,
        )

        assert first.status == "ok"
        assert second.status == "ok"
        assert first.data["centre"] == centre
        assert second.data["centre"] == centre
        assert len(motion.streams) == 2
        assert all(stream[-1] == centre for stream in motion.streams)
        assert len(motion.targets) == 2
        assert all(target.joints == centre for target in motion.targets)
        assert all(target.max_velocity == 30.0 for target in motion.targets)
        assert all(target.anticipation is False for target in motion.targets)

        # The oscillation section is identical across runs even though the
        # measured starting poses differ, proving there is no cumulative base.
        sway_frame_count = 50
        assert motion.streams[0][-sway_frame_count:] == motion.streams[1][-sway_frame_count:]

    asyncio.run(run())
