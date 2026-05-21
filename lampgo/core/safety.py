"""SafetyKernel — the single gate between motion commands and hardware.

Every frame computed by the MotionRuntime passes through this kernel
before reaching the HAL.  The kernel enforces position limits, velocity
caps, and provides persistent emergency-stop state.
"""

from __future__ import annotations

import time

import structlog

from lampgo.core.config import SafetyConfig
from lampgo.core.types import JointState, MotionTarget, SafetyRejection

logger = structlog.get_logger(__name__)


class SafetyKernel:
    _BUS_FAIL_THRESHOLD = 5

    def __init__(self, config: SafetyConfig) -> None:
        self._config = config
        self._estopped = False
        self._estop_reason: str | None = None
        self._estop_time: float | None = None
        self._bus_healthy = True
        self._consecutive_bus_failures = 0

    # ------------------------------------------------------------------
    # Target-level validation (before motion planning)
    # ------------------------------------------------------------------

    def validate_target(self, current: JointState, target: MotionTarget) -> MotionTarget | SafetyRejection:
        """Check that a requested target is within limits. Returns a clamped
        MotionTarget on success or a SafetyRejection on hard failure."""
        if self._estopped:
            return SafetyRejection(reason="e-stop active")

        clamped_joints: dict[str, float] = {}
        for joint, value in target.joints.items():
            limits = self._config.joint_limits.get(joint)
            if limits is None:
                return SafetyRejection(reason="unknown joint", joint=joint)
            clamped = max(limits.min, min(limits.max, value))
            if clamped != value:
                logger.warning(
                    "safety.target_clamped",
                    joint=joint,
                    requested=value,
                    clamped=clamped,
                )
            clamped_joints[joint] = clamped

        return MotionTarget(
            joints=clamped_joints,
            max_velocity=target.max_velocity,
            max_acceleration=target.max_acceleration,
            style=target.style,
            anticipation=target.anticipation,
        )

    def clamp_positions(self, current: JointState, positions: dict[str, float]) -> dict[str, float]:
        """Clamp raw joint positions without applying per-tick velocity limits.

        This is used for prerecorded playback, where each CSV frame is already a
        discrete target sampled at its own FPS. Re-applying control-tick velocity
        limits here distorts the original motion.
        """
        if self._estopped:
            return dict(current.positions)

        safe: dict[str, float] = {}
        for joint, value in positions.items():
            limits = self._config.joint_limits.get(joint)
            if limits is None:
                safe[joint] = current.get(joint)
                continue

            clamped = max(limits.min, min(limits.max, value))
            if clamped != value:
                logger.warning(
                    "safety.playback_clamped",
                    joint=joint,
                    requested=value,
                    clamped=clamped,
                )
            safe[joint] = clamped

        return safe

    # ------------------------------------------------------------------
    # Frame-level validation (every control tick)
    # ------------------------------------------------------------------

    def validate_frame(
        self,
        current: JointState,
        next_frame: dict[str, float],
        dt: float,
        clip_events: list[dict[str, float | str]] | None = None,
    ) -> dict[str, float]:
        """Clamp a single interpolation frame in-place. Always returns a safe
        frame — never raises, never skips. Called from the control thread."""
        if self._estopped:
            return dict(current.positions)

        safe: dict[str, float] = {}
        for joint, value in next_frame.items():
            limits = self._config.joint_limits.get(joint)
            if limits is None:
                safe[joint] = current.get(joint)
                continue

            value = max(limits.min, min(limits.max, value))

            if dt > 0:
                prev = current.get(joint, value)
                raw_velocity = abs(value - prev) / dt
                if raw_velocity > self._config.max_velocity:
                    requested = value
                    direction = 1.0 if value > prev else -1.0
                    value = prev + direction * self._config.max_velocity * dt
                    value = max(limits.min, min(limits.max, value))
                    clamped_velocity = abs(value - prev) / dt
                    retained_ratio = (
                        clamped_velocity / raw_velocity if raw_velocity > 1e-9 else 1.0
                    )
                    clip_ratio = 1.0 - retained_ratio
                    if clip_events is not None:
                        clip_events.append(
                            {
                                "joint": joint,
                                "requested_velocity": raw_velocity,
                                "allowed_velocity": clamped_velocity,
                                "retained_ratio": retained_ratio,
                                "clip_ratio": clip_ratio,
                                "requested_value": requested,
                                "clamped_value": value,
                            }
                        )
                    logger.debug(
                        "safety.velocity_clamped",
                        joint=joint,
                        requested_velocity=raw_velocity,
                        allowed_velocity=clamped_velocity,
                        retained_ratio=retained_ratio,
                        clip_ratio=clip_ratio,
                    )

            safe[joint] = value

        return safe

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------

    def estop(self, reason: str = "manual") -> None:
        if not self._estopped:
            self._estopped = True
            self._estop_reason = reason
            self._estop_time = time.monotonic()
            logger.critical("safety.estop", reason=reason)

    def reset_estop(self) -> None:
        if self._estopped:
            logger.info("safety.estop_reset", was_reason=self._estop_reason)
            self._estopped = False
            self._estop_reason = None
            self._estop_time = None

    def is_estopped(self) -> bool:
        return self._estopped

    @property
    def last_estop_reason(self) -> str | None:
        return self._estop_reason

    # ------------------------------------------------------------------
    # Bus health reporting
    # ------------------------------------------------------------------

    def report_bus_health(self, connected: bool) -> None:
        if connected:
            if self._consecutive_bus_failures > 0:
                logger.debug("safety.bus_recovered", after_failures=self._consecutive_bus_failures)
            self._consecutive_bus_failures = 0
            if self._estopped and self._estop_reason == "serial bus disconnected":
                self.reset_estop()
                logger.info("safety.auto_reset_estop", reason="bus recovered")
        else:
            self._consecutive_bus_failures += 1
            if self._consecutive_bus_failures >= self._BUS_FAIL_THRESHOLD and not self._estopped:
                self.estop(reason="serial bus disconnected")
                logger.error(
                    "safety.bus_estop",
                    consecutive_failures=self._consecutive_bus_failures,
                )
        self._bus_healthy = connected
