from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from lampgo.recordings import (
    build_recording_actions_prompt,
    list_recording_catalog,
    read_recording_metadata,
    write_recording_description,
)
from lampgo.skills.builtin.playback_skills import PlayRecordingSkill


def _write_recording_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
        "0.0,0,0,0,0,0\n"
        "0.1,1,2,3,4,5\n",
        encoding="utf-8",
    )


def test_recording_metadata_supports_expression_preset(tmp_path: Path) -> None:
    csv_path = tmp_path / "user" / "happy_wave.csv"
    _write_recording_csv(csv_path)

    write_recording_description(csv_path, "开心挥手，适合打招呼", expression="heart", expression_preset="happy")

    metadata = read_recording_metadata(csv_path)
    assert metadata == {
        "description": "开心挥手，适合打招呼",
        "expression": "heart",
        "expression_preset": "happy",
    }

    catalog = list_recording_catalog(tmp_path)
    assert catalog == [
        {
            "name": "happy_wave",
            "source": "user",
            "path": str(csv_path),
            "description": "开心挥手，适合打招呼",
            "expression": "heart",
            "expression_preset": "happy",
        }
    ]

    prompt = build_recording_actions_prompt(tmp_path)
    assert "expression_preset=happy | expression=heart" in prompt
    assert "pass it to `play_recording` so C6 eyes" in prompt


class _FakeMotion:
    def __init__(self) -> None:
        self.stream_calls: list[dict] = []
        self.return_targets = []
        self.stop_calls = 0

    def stream_frames(self, frames, *, fps: int, playback_mode: str):
        self.stream_calls.append({"frames": frames, "fps": fps, "playback_mode": playback_mode})
        done = threading.Event()
        done.set()
        return done

    def move_to(self, target):
        self.return_targets.append(target)
        done = threading.Event()
        done.set()
        return done

    def stop_immediate(self) -> None:
        self.stop_calls += 1


class _FakeLed:
    is_connected = True

    def __init__(self) -> None:
        self.preset_calls: list[tuple[str, dict]] = []
        self.mode_calls: list[str] = []
        self.stop_calls = 0

    def play_expression(self, expression_id: str, **overrides):
        self.preset_calls.append((expression_id, overrides))
        return True, {"preset_id": expression_id}

    def set_mode(self, expression: str) -> bool:
        self.mode_calls.append(expression)
        return True

    def stop_expression(self) -> bool:
        self.stop_calls += 1
        return True


def test_play_recording_uses_expression_preset_before_legacy_led(tmp_path: Path) -> None:
    _write_recording_csv(tmp_path / "wave.csv")
    motion = _FakeMotion()
    led = _FakeLed()
    skill = PlayRecordingSkill(tmp_path)

    result = asyncio.run(
        skill.execute(
            SimpleNamespace(motion=motion, led=led),
            name="wave",
            expression="heart",
            expression_preset="happy",
            playback_mode="cleaned",
        )
    )

    assert result.status == "ok"
    assert result.data["expression_preset"] == "happy"
    assert result.data["expression"] == "heart"
    assert led.preset_calls == [("happy", {"playback": "loop"})]
    assert led.stop_calls == 1
    assert led.mode_calls == []
    assert motion.stream_calls[0]["playback_mode"] == "cleaned"
    assert motion.return_targets


def test_play_recording_keeps_legacy_led_expression(tmp_path: Path) -> None:
    _write_recording_csv(tmp_path / "wave.csv")
    motion = _FakeMotion()
    led = _FakeLed()
    skill = PlayRecordingSkill(tmp_path)

    result = asyncio.run(
        skill.execute(
            SimpleNamespace(motion=motion, led=led),
            name="wave",
            expression="heart",
            playback_mode="cleaned",
        )
    )

    assert result.status == "ok"
    assert result.data["expression_preset"] is None
    assert led.preset_calls == []
    assert led.stop_calls == 0
    assert led.mode_calls == ["heart"]


def test_play_recording_cancel_stops_motion_and_looping_expression(tmp_path: Path) -> None:
    motion = _FakeMotion()
    led = _FakeLed()
    skill = PlayRecordingSkill(tmp_path)
    skill._motion = motion
    skill._expression_controller = led

    asyncio.run(skill.cancel())

    assert motion.stop_calls == 1
    assert led.stop_calls == 1
    assert skill._expression_controller is None
