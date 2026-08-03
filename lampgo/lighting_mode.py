"""Persistent conventional desk-lamp operating mode.

The recorded action is treated as a taught pose, not as a trajectory to loop.
Entering the mode moves once to the stable tail pose and applies steady white
light.  The server owns interruption/return-safe orchestration so the skill
executor remains available while the lamp is holding position.
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from lampgo.core.hal import MotorStartupState
from lampgo.core.types import JOINT_NAMES, MotionTarget, SkillResult

logger = structlog.get_logger(__name__)

LIGHTING_RECORDING_NAME = "照明模式"
LIGHTING_SETTLE_WINDOW_S = 3.0
LIGHTING_MAX_SETTLE_SPAN_DEG = 0.5
LIGHTING_MOVE_VELOCITY_DPS = 30.0
LIGHTING_MOVE_TIMEOUT_S = 30.0
LIGHTING_POSE_WARNING_TOLERANCE_DEG = 2.0
# This mode is used as a visual filming pose, not a precision positioning task.
# Keep a generous acceptance envelope while still rejecting a clearly stalled
# or unrelated pose.
LIGHTING_POSE_ACCEPTANCE_TOLERANCE_DEG = 8.0


@dataclass(frozen=True)
class TaughtLightingPose:
    joints: dict[str, float]
    source_path: Path
    source_frames: int
    settle_frames: int
    settle_window_s: float


def load_taught_lighting_pose(
    recordings_dir: Path,
    *,
    recording_name: str = LIGHTING_RECORDING_NAME,
    settle_window_s: float = LIGHTING_SETTLE_WINDOW_S,
    max_settle_span_deg: float = LIGHTING_MAX_SETTLE_SPAN_DEG,
) -> TaughtLightingPose:
    """Extract a stable fixed pose from the tail of a teach-recording CSV."""
    path = Path(recordings_dir) / "user" / f"{recording_name}.csv"
    if not path.is_file():
        raise ValueError(f"照明姿态录制不存在：{path}")

    samples: list[tuple[float, dict[str, float]]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            try:
                timestamp = float(row.get("timestamp") or index / 30.0)
                joints = {
                    joint: float(row.get(f"{joint}.pos", row.get(joint, "")))
                    for joint in JOINT_NAMES
                }
            except (TypeError, ValueError) as exc:
                raise ValueError(f"照明姿态录制第 {index + 2} 行无效") from exc
            samples.append((timestamp, joints))

    if len(samples) < 2:
        raise ValueError("照明姿态录制没有足够的有效帧")

    final_timestamp = samples[-1][0]
    window_start = final_timestamp - max(0.5, float(settle_window_s))
    settled = [joints for timestamp, joints in samples if timestamp >= window_start]
    if len(settled) < 10:
        raise ValueError("照明姿态录制末段稳定帧不足")

    pose: dict[str, float] = {}
    for joint in JOINT_NAMES:
        values = [frame[joint] for frame in settled]
        span = max(values) - min(values)
        if span > max_settle_span_deg:
            raise ValueError(
                f"照明姿态录制末段仍在移动：{joint} 波动 {span:.2f}°，"
                f"允许值 {max_settle_span_deg:.2f}°"
            )
        pose[joint] = float(statistics.median(values))

    return TaughtLightingPose(
        joints=pose,
        source_path=path,
        source_frames=len(samples),
        settle_frames=len(settled),
        settle_window_s=max(0.5, float(settle_window_s)),
    )


async def _await_motion(done_event: Any, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while not done_event.is_set():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


class LightingModeController:
    """Own the long-lived lighting state without occupying SkillExecutor."""

    def __init__(
        self,
        *,
        recordings_dir: Path,
        motion: Any,
        led: Any,
        safety: Any,
        hal: Any,
        clock: Any | None,
        electronic_ocean: Any | None,
        brightness: Callable[[], int],
        no_hw: Callable[[], bool],
    ) -> None:
        self._recordings_dir = Path(recordings_dir)
        self._motion = motion
        self._led = led
        self._safety = safety
        self._hal = hal
        self._clock = clock
        self._electronic_ocean = electronic_ocean
        self._brightness = brightness
        self._no_hw = no_hw
        self._lock = asyncio.Lock()
        self._state = "normal"
        self._target_pose: dict[str, float] = {}
        self._entered_at: float | None = None
        self._last_exit_reason = ""
        self._last_error = ""
        self._last_return_safe_status = "not_run"
        self._last_brightness = 0
        self._last_pose_errors: dict[str, float] = {}

    def bind_runtime(self, motion: Any, hal: Any | None = None) -> None:
        """Keep the controller aligned with a hot-reloaded/virtual runtime."""
        self._motion = motion
        if hal is not None:
            self._hal = hal

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == "active"

    @property
    def is_engaged(self) -> bool:
        return self._state in {"entering", "active", "exiting"}

    @property
    def blocks_autonomous_motion(self) -> bool:
        return self.is_engaged

    @property
    def blocks_automatic_led(self) -> bool:
        return self._state in {"entering", "active"}

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": "lighting",
            "label": "台灯常规模式",
            "state": self._state,
            "active": self.is_active,
            "engaged": self.is_engaged,
            "source_recording": LIGHTING_RECORDING_NAME,
            "target_pose": dict(self._target_pose),
            "brightness": self._last_brightness,
            "entered_at": self._entered_at,
            "last_exit_reason": self._last_exit_reason,
            "last_error": self._last_error,
            "pose_errors": dict(self._last_pose_errors),
            "return_safe_status": self._last_return_safe_status,
        }

    def _preflight_error(self) -> str:
        if self._no_hw() or bool(getattr(self._motion, "is_virtual", False)):
            return "台灯常规模式需要真实机械臂硬件"
        if not bool(getattr(self._hal, "is_connected", False)):
            return "机械臂硬件未连接"
        startup_state = getattr(self._hal, "startup_state", MotorStartupState.READY)
        startup_value = getattr(startup_state, "value", str(startup_state))
        if startup_value == MotorStartupState.RECOVERY_REQUIRED.value:
            return str(getattr(self._hal, "recovery_reason", "") or "机械臂需要先完成安全恢复")
        if startup_value == MotorStartupState.RECOVERING.value:
            recovery_error = str(getattr(self._motion, "recovery_error", "") or "").strip()
            return recovery_error or "上一次 return_safe 安全恢复未完成，机械臂当前仍在恢复保护状态"
        if startup_value != MotorStartupState.READY.value:
            return f"机械臂启动状态异常：{startup_value}"
        if bool(self._safety.is_estopped()):
            return "安全急停处于激活状态"
        if not bool(getattr(self._led, "is_connected", False)):
            return "ESP32-S3 LED 未连接"
        return ""

    def _ensure_motion_runtime(self) -> None:
        """Repair a stopped runtime only after HAL has positively reported READY."""
        if bool(getattr(self._motion, "is_running", False)):
            return
        self._motion.start()
        if not bool(getattr(self._motion, "is_running", False)):
            raise RuntimeError("机械臂硬件已就绪，但运动运行时启动失败")

    def _deactivate_background_led(self) -> None:
        if self._clock is not None:
            self._clock.deactivate()
        if self._electronic_ocean is not None:
            self._electronic_ocean.deactivate()

    async def enter(self) -> SkillResult:
        async with self._lock:
            if self._state == "active":
                return SkillResult(status="ok", data={**self.snapshot(), "already_active": True})
            if self._state in {"entering", "exiting"}:
                return SkillResult(status="error", message=f"照明模式当前正在{self._state}")

            preflight_error = self._preflight_error()
            if preflight_error:
                self._last_error = preflight_error
                return SkillResult(status="error", message=preflight_error, data=self.snapshot())

            self._state = "entering"
            self._last_error = ""
            self._last_pose_errors = {}
            self._last_return_safe_status = "not_run"
            try:
                self._ensure_motion_runtime()
                taught = load_taught_lighting_pose(self._recordings_dir)
                self._target_pose = dict(taught.joints)
                self._deactivate_background_led()

                done = self._motion.move_to(
                    MotionTarget(
                        joints=dict(taught.joints),
                        max_velocity=LIGHTING_MOVE_VELOCITY_DPS,
                        anticipation=False,
                    )
                )
                if not await _await_motion(done, LIGHTING_MOVE_TIMEOUT_S):
                    raise RuntimeError("移动到照明姿态超时")

                actual = dict(getattr(self._motion.current_state, "positions", {}) or {})
                pose_errors = {
                    joint: round(actual.get(joint, target) - target, 2)
                    for joint, target in taught.joints.items()
                    if abs(actual.get(joint, target) - target)
                    > LIGHTING_POSE_WARNING_TOLERANCE_DEG
                }
                self._last_pose_errors = pose_errors
                blocking_errors = {
                    joint: error
                    for joint, error in pose_errors.items()
                    if abs(error) > LIGHTING_POSE_ACCEPTANCE_TOLERANCE_DEG
                }
                if blocking_errors:
                    raise RuntimeError(f"明显偏离照明姿态，关节误差：{blocking_errors}")
                if pose_errors:
                    logger.warning(
                        "lighting_mode.pose_within_video_tolerance",
                        errors=pose_errors,
                        acceptance_tolerance_deg=LIGHTING_POSE_ACCEPTANCE_TOLERANCE_DEG,
                    )

                brightness = max(1, min(96, int(self._brightness())))
                if not self._led.set_brightness(brightness):
                    raise RuntimeError("照明姿态已到达，但 LED 亮度设置失败")
                if not self._led.set_mode("white"):
                    raise RuntimeError("照明姿态已到达，但白色灯光设置失败")

                self._last_brightness = brightness
                self._entered_at = time.time()
                self._state = "active"
                logger.info(
                    "lighting_mode.entered",
                    target=taught.joints,
                    brightness=brightness,
                    source=str(taught.source_path),
                )
                return SkillResult(
                    status="ok",
                    data={
                        **self.snapshot(),
                        "actual_pose": actual,
                        "pose_warning": pose_errors,
                        "source_frames": taught.source_frames,
                        "settle_frames": taught.settle_frames,
                    },
                )
            except asyncio.CancelledError:
                self._state = "normal"
                self._entered_at = None
                self._last_exit_reason = "enter_cancelled"
                raise
            except Exception as exc:
                self._state = "normal"
                self._entered_at = None
                self._last_error = str(exc)
                logger.warning("lighting_mode.enter_failed", error=str(exc))
                return SkillResult(status="error", message=str(exc), data=self.snapshot())

    async def cancel_enter(self) -> None:
        if self._state != "entering":
            return
        self._motion.stop_immediate()
        self._led.off()
        self._state = "normal"
        self._entered_at = None
        self._last_exit_reason = "enter_cancelled"

    async def begin_exit(self, *, reason: str, force: bool = False) -> bool:
        async with self._lock:
            if not force and not self.is_engaged:
                return False
            self._state = "exiting"
            self._last_exit_reason = str(reason or "user")
            self._last_return_safe_status = "running"
            self._deactivate_background_led()
            if not self._led.off():
                logger.warning("lighting_mode.led_off_failed", reason=reason)
            logger.info("lighting_mode.exiting", reason=reason)
            return True

    async def finish_exit(self, *, return_safe_status: str, error: str = "") -> None:
        async with self._lock:
            self._state = "normal"
            self._entered_at = None
            self._last_return_safe_status = return_safe_status
            self._last_error = str(error or "")
            logger.info(
                "lighting_mode.exited",
                reason=self._last_exit_reason,
                return_safe_status=return_safe_status,
                error=error,
            )

    async def force_exit(self, *, reason: str, turn_off_led: bool = True) -> None:
        """Leave mode without motion; used only for estop/shutdown/fault paths."""
        async with self._lock:
            if not self.is_engaged:
                return
            self._state = "normal"
            self._entered_at = None
            self._last_exit_reason = reason
            self._last_return_safe_status = "skipped"
            self._deactivate_background_led()
            if turn_off_led:
                self._led.off()
            logger.info("lighting_mode.force_exited", reason=reason)
