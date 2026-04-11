"""Biomimetic motion style — spring-damper parameters for joint interpolation.

Style presets control how the SecondOrderDynamics filter in MotionRuntime
responds to a new MOVE_TO target.  See lampgo/core/spring.py for the
underlying physics model.

Style parameters
----------------
f  : Natural frequency (Hz).  Higher = snappier response.
z  : Damping ratio.
       z = 1.0  critical damping  (smooth, no overshoot)
       z < 1.0  underdamped       (overshoot = "Q弹" bounce feel)
       z > 1.0  overdamped        (sluggish, hesitant)

Easing functions (ease_in_out_cubic etc.) are kept *only* for
trajectory.py (generate_waypoint_frames), which pre-bakes parametric
keyframe interpolation.  They are NOT part of MotionStyle anymore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Easing functions — kept for trajectory.py generate_waypoint_frames only
# ---------------------------------------------------------------------------


def ease_linear(t: float) -> float:
    return max(0.0, min(1.0, t))


def ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4 * t * t * t
    return 1 - (-2 * t + 2) ** 3 / 2


def ease_in_out_quad(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 2 * t * t
    return 1 - (-2 * t + 2) ** 2 / 2


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t


def ease_out_back(t: float, overshoot: float = 0.15) -> float:
    """Overshoot past 1 then settle — follow-through."""
    t = max(0.0, min(1.0, t))
    c = 1 + overshoot
    t_adj = t - 1.0
    return 1 + c * (t_adj**3) + (c - 1) * (t_adj**2)


EASE_FUNCTIONS: dict[str, Callable[[float], float]] = {
    "linear": ease_linear,
    "ease_in_out_cubic": ease_in_out_cubic,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_out_cubic": ease_out_cubic,
    "ease_in_cubic": ease_in_cubic,
}


# ---------------------------------------------------------------------------
# MotionStyle — spring-damper parameters for MOVE_TO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionStyle:
    """Spring-damper parameters for a single MOVE_TO motion.

    Attributes
    ----------
    f : float
        Natural frequency in Hz.  Controls how quickly the spring
        approaches the target.  Scaled down automatically for large
        moves to stay within the SafetyKernel velocity cap (see
        :func:`~lampgo.core.spring.cap_spring_f`).
    z : float
        Damping ratio.  Always preserved regardless of move distance —
        it is the pure "character" of the motion style.
    """

    f: float = 1.5
    z: float = 1.0


DEFAULT_STYLE_NAME = "gentle"

STYLE_PRESETS: dict[str, MotionStyle] = {
    # Smooth, no overshoot — default for most LLM-driven moves
    "gentle":    MotionStyle(f=1.5,  z=1.0),
    # Quick with a light bounce — assertive actions
    "confident": MotionStyle(f=2.5,  z=0.6),
    # Slow, curious approach with visible overshoot
    "curious":   MotionStyle(f=1.0,  z=0.7),
    # Fast, multi-bounce — celebratory / playful
    "bouncy":    MotionStyle(f=3.0,  z=0.35),
    # Over-damped slug — uncertain, hesitant
    "hesitant":  MotionStyle(f=0.8,  z=1.5),
    # Near-direct pass-through — visual servo / reactive control
    "linear":    MotionStyle(f=8.0,  z=1.0),
}


def resolve_style_name(name: str | None, default_name: str = DEFAULT_STYLE_NAME) -> str:
    if not name or not name.strip():
        key = (default_name or DEFAULT_STYLE_NAME).strip().lower()
    else:
        key = name.strip().lower()
    if key not in STYLE_PRESETS:
        return DEFAULT_STYLE_NAME
    return key


def get_motion_style(name: str | None, default_name: str = DEFAULT_STYLE_NAME) -> MotionStyle:
    key = resolve_style_name(name, default_name)
    return STYLE_PRESETS[key]
