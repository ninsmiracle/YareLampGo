"""Tests for local visual cat teaser behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from lampgo.core.events import EventBus
from lampgo.core.types import JointState
from lampgo.perception.cat_teaser import (
    CatPlayObservation,
    CatPlayStateEstimator,
    CatToyTracker,
    MarkerDetection,
)
from lampgo.skills.builtin import cat_teaser as cat_skill_mod
from lampgo.skills.builtin.cat_teaser import CatTeaserSkill


def _magenta_frame(*, with_marker: bool = True, motion_size: int = 0) -> np.ndarray:
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    if with_marker:
        cv2.circle(frame, (160, 120), 16, (255, 0, 255), -1)
    if motion_size:
        cv2.rectangle(frame, (176, 105), (176 + motion_size, 105 + motion_size), (255, 255, 255), -1)
    return frame


def test_cat_toy_tracker_detects_magenta_marker_and_blank_frame() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")

    detection = tracker.track(_magenta_frame())

    assert detection is not None
    assert detection.confidence > 0.2
    assert 0.45 < detection.normalized_x < 0.55
    assert 0.45 < detection.normalized_y < 0.55
    assert tracker.track(_magenta_frame(with_marker=False)) is None


def test_cat_play_state_estimator_transitions_on_synthetic_motion() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    teasing = estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.0)
    engaged = estimator.update(_magenta_frame(motion_size=24), tracker.track(_magenta_frame()), timestamp=1.2)
    pounce = estimator.update(_magenta_frame(motion_size=96), tracker.track(_magenta_frame()), timestamp=1.4)
    caught = estimator.update(_magenta_frame(with_marker=False, motion_size=96), None, timestamp=1.6)

    rest = caught
    for idx in range(12):
        rest = estimator.update(_magenta_frame(with_marker=False), None, timestamp=4.0 + idx * 0.3)

    assert teasing.state == "teasing"
    assert engaged.state in {"engaged", "pounce"}
    assert pounce.state == "pounce"
    assert caught.state == "caught"
    assert rest.state == "rest"


class _FakeFrameSource:
    device_label = "fake://camera"

    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.reads = 0

    def start(self) -> None:
        self.started = True

    def read(self):
        self.reads += 1
        return object()

    def close(self) -> None:
        self.closed = True


class _FakeTracker:
    def __init__(self, *, marker_color: str = "magenta") -> None:
        self.marker_color = marker_color

    def track(self, frame) -> MarkerDetection:
        del frame
        return MarkerDetection(
            x=170,
            y=110,
            radius=14,
            area=500,
            confidence=0.9,
            frame_width=320,
            frame_height=240,
        )


class _FakeEstimator:
    def __init__(self) -> None:
        self.states = iter(["teasing", "engaged", "pounce", "caught", "unsafe_close"])

    def update(self, frame, marker, *, timestamp=None) -> CatPlayObservation:
        del frame, timestamp
        state = next(self.states, "engaged")
        score = {"teasing": 0.1, "engaged": 0.55, "pounce": 0.8, "caught": 0.7, "unsafe_close": 0.0}[state]
        return CatPlayObservation(
            state=state,
            marker=marker,
            motion_energy=0.1,
            engagement_score=score,
            motion_centroid=(0.5, 0.5),
            timestamp=1.0,
        )


class _FakeMotion:
    is_running = True

    def __init__(self) -> None:
        self.current_state = JointState(positions={"base_yaw": 0.0, "base_pitch": 0.0, "wrist_pitch": 0.0})
        self.targets = []
        self.stopped = False

    def update_target(self, target) -> None:
        self.targets.append(target)

    def stop_smooth(self) -> None:
        self.stopped = True


def _fake_context(motion: _FakeMotion):
    return SimpleNamespace(
        motion=motion,
        led=SimpleNamespace(is_connected=False),
        events=EventBus(),
        state=motion.current_state,
    )


@pytest.mark.asyncio
async def test_cat_teaser_skill_emits_bounded_linear_targets(monkeypatch) -> None:
    source = _FakeFrameSource()
    monkeypatch.setattr(cat_skill_mod, "CatToyTracker", _FakeTracker)
    monkeypatch.setattr(cat_skill_mod, "CatPlayStateEstimator", _FakeEstimator)
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    result = await skill.execute(
        _fake_context(motion),
        duration=0.35,
        camera_fps=12,
        max_yaw=25,
        max_pitch=14,
        max_wrist_pitch=8,
    )

    assert result.status == "ok"
    assert result.data["frames"] >= 2
    assert result.data["marker_seen"] >= 2
    assert source.closed is True
    assert motion.stopped is True
    assert motion.targets
    for target in motion.targets:
        assert target.style == "linear"
        assert abs(target.joints["base_yaw"]) <= 25.0
        assert abs(target.joints["base_pitch"]) <= 14.0
        assert abs(target.joints["wrist_pitch"]) <= 8.0


@pytest.mark.asyncio
async def test_cat_teaser_skill_cancels_cleanly(monkeypatch) -> None:
    source = _FakeFrameSource()
    monkeypatch.setattr(cat_skill_mod, "CatToyTracker", _FakeTracker)
    monkeypatch.setattr(cat_skill_mod, "CatPlayStateEstimator", _FakeEstimator)
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    task = asyncio.create_task(skill.execute(_fake_context(motion), duration=5, camera_fps=12))
    while source.reads == 0:
        await asyncio.sleep(0.01)
    await skill.cancel()
    result = await task

    assert result.status == "cancelled"
    assert result.data["stop_reason"] == "cancelled"
    assert source.closed is True
    assert motion.stopped is True


@pytest.mark.asyncio
async def test_cat_teaser_skill_rejects_unknown_marker_color() -> None:
    source = _FakeFrameSource()
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    result = await skill.execute(_fake_context(motion), marker_color="orange", duration=1)

    assert result.status == "error"
    assert "Unsupported marker_color" in result.message
    assert source.started is False
    assert motion.stopped is False


def test_cat_teaser_is_registered_and_exposes_parameters() -> None:
    from lampgo.core.config import LampgoConfig
    from lampgo.server import LampgoServer

    server = LampgoServer(LampgoConfig(no_hw=True))
    server._register_builtin_skills()

    skills = server._handle_skills()["result"]["skills"]
    cat_teaser = next(skill for skill in skills if skill["skill_id"] == "cat_teaser")

    assert cat_teaser["label"] == "逗猫棒互动"
    assert cat_teaser["parameters"]["marker_color"]["default"] == "magenta"
    assert cat_teaser["parameters"]["duration"]["default"] == 60.0
