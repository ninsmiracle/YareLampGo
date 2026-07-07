"""Tests for local visual cat teaser behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from lampgo.core.config import CameraConfig
from lampgo.core.events import EventBus
from lampgo.core.types import JointState
from lampgo.perception import cat_teaser as cat_perception_mod
from lampgo.perception.cat_teaser import (
    CatPlayObservation,
    CatPlayStateEstimator,
    CatTeaserCameraError,
    CatTeaserDebugView,
    CatTeaserFrameSource,
    CatToyTracker,
    MarkerDetection,
)
from lampgo.skills.builtin import cat_teaser as cat_skill_mod
from lampgo.skills.builtin.cat_teaser import CatTeaserSkill

_POUNCE_RECTS = (
    (96, 56, 40, 40),
    (96, 144, 40, 40),
    (184, 56, 40, 40),
    (184, 144, 40, 40),
)


def _magenta_frame(
    *,
    with_marker: bool = True,
    motion_size: int = 0,
    marker_center: tuple[int, int] = (160, 120),
    motion_origin: tuple[int, int] = (176, 105),
    motion_rect: tuple[int, int, int, int] | None = None,
    motion_rects: tuple[tuple[int, int, int, int], ...] = (),
) -> np.ndarray:
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    if with_marker:
        cv2.circle(frame, marker_center, 16, (255, 0, 255), -1)
    if motion_rect is not None:
        x, y, width, height = motion_rect
        cv2.rectangle(frame, (x, y), (x + width, y + height), (255, 255, 255), -1)
    for x, y, width, height in motion_rects:
        cv2.rectangle(frame, (x, y), (x + width, y + height), (255, 255, 255), -1)
    if motion_size:
        x, y = motion_origin
        cv2.rectangle(frame, (x, y), (x + motion_size, y + motion_size), (255, 255, 255), -1)
    return frame


def _red_frame(*, with_marker: bool = True) -> np.ndarray:
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    if with_marker:
        cv2.circle(frame, (160, 120), 16, (0, 0, 255), -1)
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


def test_cat_toy_tracker_detects_red_marker() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="red")

    detection = tracker.track(_red_frame())

    assert detection is not None
    assert detection.confidence > 0.2


def test_cat_play_state_estimator_transitions_on_synthetic_motion() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    teasing = estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.0)
    engaged = estimator.update(_magenta_frame(motion_size=44), tracker.track(_magenta_frame()), timestamp=1.2)
    estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.3)
    estimator.update(
        _magenta_frame(motion_rects=_POUNCE_RECTS),
        tracker.track(_magenta_frame()),
        timestamp=1.4,
    )
    pounce = estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.6)
    estimator.update(_magenta_frame(with_marker=False, motion_size=96, motion_origin=(96, 64)), None, timestamp=1.8)
    estimator.update(_magenta_frame(with_marker=False), None, timestamp=2.0)
    caught = estimator.update(
        _magenta_frame(with_marker=False, motion_size=96, motion_origin=(96, 64)),
        None,
        timestamp=2.2,
    )

    rest = caught
    for idx in range(12):
        rest = estimator.update(_magenta_frame(with_marker=False), None, timestamp=4.0 + idx * 0.3)

    assert teasing.state == "teasing"
    assert engaged.state in {"engaged", "pounce"}
    assert pounce.state == "pounce"
    assert caught.state == "caught"
    assert rest.state == "rest"


def test_cat_play_state_estimator_ignores_marker_self_motion() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    frame_1 = _magenta_frame(marker_center=(140, 120))
    frame_2 = _magenta_frame(marker_center=(190, 120))
    frame_3 = _magenta_frame(marker_center=(220, 120))

    estimator.update(frame_1, tracker.track(frame_1), timestamp=1.0)
    moved = estimator.update(frame_2, tracker.track(frame_2), timestamp=1.2)
    moved_again = estimator.update(frame_3, tracker.track(frame_3), timestamp=1.4)

    assert moved.motion_energy < 0.045
    assert moved.state == "teasing"
    assert moved_again.state == "teasing"


def test_cat_play_state_estimator_detects_visible_marker_contact() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    baseline = _magenta_frame()
    estimator.update(baseline, tracker.track(baseline), timestamp=1.0)
    touched = _magenta_frame(marker_center=(164, 122), motion_rect=(178, 96, 70, 70))

    observation = estimator.update(touched, tracker.track(touched), timestamp=1.2)

    assert observation.marker is not None
    assert observation.motion_energy > 0.15
    assert observation.contact_motion_energy > 0.27
    assert observation.marker_disturbance > 0.05
    assert observation.state == "caught"


def test_cat_play_state_estimator_ignores_near_tip_hand_when_marker_is_stable() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    baseline = _magenta_frame()
    estimator.update(baseline, tracker.track(baseline), timestamp=1.0)
    near_tip_hand = _magenta_frame(motion_rect=(178, 96, 70, 70))

    observation = estimator.update(near_tip_hand, tracker.track(near_tip_hand), timestamp=1.2)

    assert observation.marker is not None
    assert observation.contact_motion_energy > 0.24
    assert observation.marker_disturbance == 0.0
    assert observation.state != "caught"


def test_cat_play_state_estimator_ignores_nearby_hand_without_tip_contact() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    baseline = _magenta_frame()
    estimator.update(baseline, tracker.track(baseline), timestamp=1.0)
    nearby_hand = _magenta_frame(motion_rect=(190, 80, 100, 100))

    observation = estimator.update(nearby_hand, tracker.track(nearby_hand), timestamp=1.2)

    assert observation.marker is not None
    assert observation.motion_energy > 0.16
    assert observation.state != "caught"


def test_cat_play_state_estimator_ignores_brief_marker_loss() -> None:
    pytest.importorskip("cv2")
    tracker = CatToyTracker(marker_color="magenta")
    estimator = CatPlayStateEstimator()

    estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.0)
    estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.1)
    estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.2)
    estimator.update(_magenta_frame(motion_rects=_POUNCE_RECTS), tracker.track(_magenta_frame()), timestamp=1.3)
    pounce = estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.4)
    lost_1 = estimator.update(_magenta_frame(with_marker=False, motion_size=96), None, timestamp=1.5)
    lost_2 = estimator.update(_magenta_frame(with_marker=False), None, timestamp=1.6)
    reacquired = estimator.update(_magenta_frame(), tracker.track(_magenta_frame()), timestamp=1.7)

    assert pounce.state == "pounce"
    assert lost_1.state == "searching"
    assert lost_2.state == "searching"
    assert reacquired.state != "caught"


def test_cat_teaser_debug_view_disabled_is_noop() -> None:
    view = CatTeaserDebugView(enabled=False)
    observation = CatPlayObservation(
        state="teasing",
        marker=None,
        motion_energy=0.0,
        engagement_score=0.0,
        motion_centroid=None,
        timestamp=1.0,
    )

    assert view.render(object(), observation, elapsed_s=0.0, marker_color="red") is False


class _FakeVideoCapture:
    def __init__(self, device, *, opened: bool = True) -> None:
        self.device = device
        self.opened = opened
        self.released = False
        self.props: dict[int, float] = {}

    def isOpened(self) -> bool:
        return self.opened

    def set(self, prop: int, value: float) -> None:
        self.props[prop] = value

    def read(self):
        return True, np.zeros((240, 320, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


class _FakeCv2:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4

    def __init__(self) -> None:
        self.devices: list[int | str] = []
        self.captures: list[_FakeVideoCapture] = []

    def VideoCapture(self, device, backend=None):  # noqa: N802 - mirror cv2 API
        del backend
        self.devices.append(device)
        cap = _FakeVideoCapture(device)
        self.captures.append(cap)
        return cap


def test_cat_teaser_frame_source_uses_local_camera_fallback(monkeypatch) -> None:
    fake_cv2 = _FakeCv2()
    monkeypatch.setattr(cat_perception_mod, "_import_cv2", lambda: fake_cv2)
    source = CatTeaserFrameSource(CameraConfig(port=""), allow_local_camera_fallback=True)

    source.start()
    frame = source.read()

    assert source.enabled is True
    assert fake_cv2.devices == [0]
    assert source.device_label == "local://0 (fallback)"
    assert frame is not None
    source.close()
    assert fake_cv2.captures[0].released is True


def test_cat_teaser_frame_source_requires_camera_without_fallback() -> None:
    source = CatTeaserFrameSource(CameraConfig(port=""))

    assert source.enabled is False
    with pytest.raises(CatTeaserCameraError):
        source.start()


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


class _ImageFrameSource(_FakeFrameSource):
    def read(self):
        self.reads += 1
        return _magenta_frame()


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


class _CaughtFirstEstimator:
    def __init__(self) -> None:
        self.states = iter(["caught", "engaged", "engaged"])

    def update(self, frame, marker, *, timestamp=None) -> CatPlayObservation:
        del frame, timestamp
        state = next(self.states, "engaged")
        return CatPlayObservation(
            state=state,
            marker=marker,
            motion_energy=0.5 if state == "caught" else 0.08,
            engagement_score=0.8 if state == "caught" else 0.4,
            motion_centroid=(0.25, 0.5),
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
        debug_view=False,
        log_events=False,
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


def test_cat_teaser_self_motion_suppression_downgrades_false_grabs() -> None:
    marker = MarkerDetection(
        x=160,
        y=120,
        radius=12,
        area=400,
        confidence=0.8,
        frame_width=320,
        frame_height=240,
    )
    caught = CatPlayObservation(
        state="caught",
        marker=None,
        motion_energy=0.4,
        engagement_score=0.7,
        motion_centroid=(0.5, 0.5),
        timestamp=1.0,
    )
    pounce = CatPlayObservation(
        state="pounce",
        marker=marker,
        motion_energy=0.25,
        engagement_score=0.7,
        motion_centroid=(0.5, 0.5),
        timestamp=1.0,
    )
    unsafe = CatPlayObservation(
        state="unsafe_close",
        marker=marker,
        motion_energy=0.25,
        engagement_score=0.0,
        motion_centroid=(0.5, 0.5),
        timestamp=1.0,
    )

    assert CatTeaserSkill._suppress_self_motion_observation(
        caught,
        now=1.0,
        suppression_until=1.2,
    ).state == "searching"
    assert CatTeaserSkill._suppress_self_motion_observation(
        pounce,
        now=1.0,
        suppression_until=1.2,
    ).state == "engaged"
    assert CatTeaserSkill._suppress_self_motion_observation(
        unsafe,
        now=1.0,
        suppression_until=1.2,
    ).state == "unsafe_close"
    assert CatTeaserSkill._suppress_self_motion_observation(
        caught,
        now=1.3,
        suppression_until=1.2,
    ).state == "caught"


@pytest.mark.asyncio
async def test_cat_teaser_skill_escape_dashes_on_caught(monkeypatch) -> None:
    source = _FakeFrameSource()
    monkeypatch.setattr(cat_skill_mod, "CatToyTracker", _FakeTracker)
    monkeypatch.setattr(cat_skill_mod, "CatPlayStateEstimator", _CaughtFirstEstimator)
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    result = await skill.execute(
        _fake_context(motion),
        duration=0.18,
        camera_fps=12,
        max_yaw=25,
        max_pitch=14,
        max_wrist_pitch=8,
        debug_view=False,
        log_events=False,
    )

    assert result.status == "ok"
    assert result.data["escape_dashes"] >= 1
    assert any(target.max_velocity >= 70.0 for target in motion.targets)
    assert any(target.joints["base_yaw"] >= 12.0 for target in motion.targets)


@pytest.mark.asyncio
async def test_cat_teaser_skill_cancels_cleanly(monkeypatch) -> None:
    source = _FakeFrameSource()
    monkeypatch.setattr(cat_skill_mod, "CatToyTracker", _FakeTracker)
    monkeypatch.setattr(cat_skill_mod, "CatPlayStateEstimator", _FakeEstimator)
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    task = asyncio.create_task(
        skill.execute(_fake_context(motion), duration=5, camera_fps=12, debug_view=False, log_events=False)
    )
    while source.reads == 0:
        await asyncio.sleep(0.01)
    await skill.cancel()
    result = await task

    assert result.status == "cancelled"
    assert result.data["stop_reason"] == "cancelled"
    assert source.closed is True
    assert motion.stopped is True


@pytest.mark.asyncio
async def test_cat_teaser_skill_prints_touch_event_log(monkeypatch, capsys) -> None:
    source = _FakeFrameSource()
    monkeypatch.setattr(cat_skill_mod, "CatToyTracker", _FakeTracker)
    monkeypatch.setattr(cat_skill_mod, "CatPlayStateEstimator", _CaughtFirstEstimator)
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    result = await skill.execute(_fake_context(motion), duration=0.4, camera_fps=12, debug_view=False)
    out = capsys.readouterr().out

    assert result.status == "ok"
    assert "有触碰动作" in out
    assert "contact=" in out
    assert "disturb=" in out
    assert result.data["event_counts"]["touch"] >= 1
    assert any(event["type"] == "touch" for event in result.data["events"])


@pytest.mark.asyncio
async def test_cat_teaser_skill_saves_camera_video(monkeypatch, tmp_path) -> None:
    cv2 = pytest.importorskip("cv2")
    source = _ImageFrameSource()
    monkeypatch.setattr(cat_skill_mod, "CatToyTracker", _FakeTracker)
    monkeypatch.setattr(cat_skill_mod, "CatPlayStateEstimator", _FakeEstimator)
    motion = _FakeMotion()
    skill = CatTeaserSkill(lambda: source)

    result = await skill.execute(
        _fake_context(motion),
        duration=0.25,
        camera_fps=12,
        debug_view=False,
        log_events=False,
        recording_dir=str(tmp_path),
    )

    recording = result.data["recording"]
    session_dir = Path(recording["path"])
    video_path = session_dir / "cat_teaser.mp4"
    frame_records = (session_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))

    assert result.status == "ok"
    assert session_dir.parent == tmp_path
    assert recording["enabled"] is True
    assert recording["video_path"] == str(video_path)
    assert video_path.exists()
    assert video_path.stat().st_size > 0
    capture = cv2.VideoCapture(str(video_path))
    try:
        assert capture.isOpened()
        ok, decoded_frame = capture.read()
        assert ok is True
        assert decoded_frame is not None
    finally:
        capture.release()
    assert recording["frames_written"] >= 1
    assert len(frame_records) == recording["frames_written"]
    first_record = json.loads(frame_records[0])
    assert first_record["frame"] == 1
    assert first_record["state"] in {"teasing", "engaged", "pounce", "caught", "unsafe_close"}
    assert metadata["marker_color"] == "red"
    assert metadata["video"] == "cat_teaser.mp4"
    assert metadata["video_codec"] == "mp4v"
    assert metadata["frames_written"] == recording["frames_written"]


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
    assert cat_teaser["parameters"]["marker_color"]["default"] == "red"
    assert cat_teaser["parameters"]["duration"]["default"] == 60.0
    assert cat_teaser["parameters"]["debug_view"]["default"] is True
    assert cat_teaser["parameters"]["log_events"]["default"] is True
    assert cat_teaser["parameters"]["save_recording"]["default"] is True
    assert cat_teaser["parameters"]["recording_dir"]["default"] == ""


def test_no_hw_server_enables_cat_teaser_local_camera_fallback() -> None:
    from lampgo.core.config import LampgoConfig
    from lampgo.server import LampgoServer

    server = LampgoServer(LampgoConfig(no_hw=True))
    source = server._make_cat_teaser_frame_source()
    status = server._handle_status()["result"]

    assert source.enabled is True
    assert source.device_label == "local://0 (fallback)"
    assert status["camera_ready"] is True
    assert status["cat_teaser_camera"] == {
        "mode": "local_no_hw_fallback",
        "label": "local://0 (fallback)",
        "fallback": True,
        "hardware_present": False,
    }
