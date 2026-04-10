"""Biomimetic motion style — easing, anticipation, overshoot, light randomness.

Maps normalized time to blend factors for joint interpolation. Used by MotionRuntime
as a layer above raw trapezoidal velocity (see ``linear`` preset for legacy behavior).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Easing — input t in [0, 1], output blend u (may exceed [0,1] for overshoot)
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


@dataclass(frozen=True)
class MotionStyle:
    """Parameters for stylized joint-space interpolation."""

    ease_fn: str = "ease_in_out_cubic"
    ease_overshoot: float = 0.0  # passed to ease_out_back when ease_fn == ease_out_back
    anticipation: float = 0.0  # 0–0.15, wind-up opposite to motion as fraction of span
    velocity_scale: float = 1.0
    settle_oscillations: int = 0  # reserved; keep at 0 — settle via ease curve, not oscillation
    randomness: float = 0.0  # 0–0.05, per-trajectory duration jitter only

    def apply_ease(self, t: float) -> float:
        if self.ease_fn == "ease_out_back":
            return ease_out_back(t, overshoot=max(0.0, self.ease_overshoot))
        fn = EASE_FUNCTIONS.get(self.ease_fn, ease_in_out_cubic)
        return fn(t)

    def peak_derivative(self) -> float:
        """Return the maximum instantaneous derivative of the ease curve on [0, 1].

        Used by TrajectoryPlan.create() to ensure the planned per-tick velocity
        never exceeds the SafetyKernel hard cap.

        Values are analytic / conservatively measured:
          - linear           : 1.0
          - ease_in_out_quad : 2.0  (at t=0.5)
          - ease_in_out_cubic: 3.0  (at t=0.5)
          - ease_out_cubic   : 3.0  (at t=0)
          - ease_in_cubic    : 3.0  (at t=1)
          - ease_out_back    : c+2 where c=1+overshoot  (at t=0)
        """
        if self.ease_fn == "linear":
            return 1.0
        if self.ease_fn == "ease_in_out_quad":
            return 2.0
        if self.ease_fn == "ease_out_back":
            c = 1.0 + max(0.0, self.ease_overshoot)
            return c + 2.0
        # ease_in_out_cubic, ease_out_cubic, ease_in_cubic — all peak at 3.0
        return 3.0


DEFAULT_STYLE_NAME = "gentle"

STYLE_PRESETS: dict[str, MotionStyle] = {
    "gentle": MotionStyle(
        ease_fn="ease_in_out_cubic",
        anticipation=0.0,
        velocity_scale=1.0,
    ),
    "confident": MotionStyle(
        ease_fn="ease_out_back",
        ease_overshoot=0.05,  # subtle follow-through, less aggressive than before
        anticipation=0.0,
        velocity_scale=1.1,
    ),
    "curious": MotionStyle(
        ease_fn="ease_in_out_cubic",
        anticipation=0.03,  # gentle wind-up
        velocity_scale=0.7,
    ),
    "bouncy": MotionStyle(
        ease_fn="ease_out_back",
        ease_overshoot=0.10,  # reduced from 0.15; ease_out_back provides natural overshoot
        anticipation=0.0,
        velocity_scale=1.1,
        settle_oscillations=0,  # removed: sinusoidal settle at 6Hz causes servo jitter
        randomness=0.02,  # duration-level jitter only, no per-tick noise
    ),
    "hesitant": MotionStyle(
        ease_fn="ease_in_out_quad",
        anticipation=0.05,  # reduced from 0.08
        velocity_scale=0.5,
    ),
    # linear = use trapezoidal step in MotionRuntime, not TrajectoryPlan
    "linear": MotionStyle(ease_fn="linear", velocity_scale=1.0),
}


def resolve_style_name(name: str | None, default_name: str = DEFAULT_STYLE_NAME) -> str:
    if not name or not name.strip():
        key = (default_name or DEFAULT_STYLE_NAME).strip().lower()
    else:
        key = name.strip().lower()
    if key == "linear":
        return "linear"
    if key not in STYLE_PRESETS:
        return DEFAULT_STYLE_NAME
    return key


def get_motion_style(name: str | None, default_name: str = DEFAULT_STYLE_NAME) -> MotionStyle:
    key = resolve_style_name(name, default_name)
    return STYLE_PRESETS[key]


# ---------------------------------------------------------------------------
# Trajectory plan — one segment per MOVE_TO (styled) or none for linear
# ---------------------------------------------------------------------------


def _blend_u(p: float, style: MotionStyle) -> float:
    """Map normalized phase p in [0,1] to blend factor u: start + u*(goal-start).

    Supports anticipation (negative u at start) and easing that may exceed 1.
    """
    anti = max(0.0, min(0.2, style.anticipation))
    f = 0.14 if anti > 0 else 0.0
    p = max(0.0, min(1.0, p))
    if f > 0 and p < f:
        w = p / f
        u_wind = -anti * w
        return u_wind
    if f >= 1.0:
        p2 = 1.0
    else:
        p2 = (p - f) / (1.0 - f) if f < 1.0 else p
    p2 = max(0.0, min(1.0, p2))
    e = style.apply_ease(p2)
    span = 1.0 + anti
    return -anti + span * e


@dataclass
class TrajectoryPlan:
    """Time-parameterized joint targets from start to goal with optional settle."""

    start: dict[str, float]
    goal: dict[str, float]
    style: MotionStyle
    main_duration: float
    settle_duration: float
    duration_jitter: float = 0.0
    elapsed: float = 0.0
    _rng: random.Random = field(default_factory=random.Random)

    @classmethod
    def create(
        cls,
        start_positions: dict[str, float],
        goal_joints: dict[str, float],
        style: MotionStyle,
        max_velocity: float,
        default_max_velocity: float,
        rng: random.Random | None = None,
        safety_max_velocity: float = 180.0,
    ) -> TrajectoryPlan:
        rng = rng or random.Random()
        vel = max(1e-3, max_velocity * max(0.2, style.velocity_scale))
        max_dist = 0.0
        start_slice: dict[str, float] = {}
        for j, g in goal_joints.items():
            s = start_positions.get(j, g)
            start_slice[j] = s
            max_dist = max(max_dist, abs(g - s))
        if max_dist < 1e-6:
            main_dur = 0.05
        else:
            main_dur = max_dist / vel
        # Ensure the peak instantaneous velocity implied by the easing curve stays
        # within the SafetyKernel hard cap (with a 10% headroom).  Without this,
        # ease functions with steep early slopes (e.g. ease_out_back) generate
        # per-tick deltas that SafetyKernel clips every single frame, destroying
        # the curve shape and causing the motion to degrade to a constant-speed crawl.
        peak_d = style.peak_derivative()
        vel_cap = safety_max_velocity * 0.9
        if vel_cap > 0 and max_dist > 1e-6:
            min_safe_dur = peak_d * max_dist / vel_cap
            main_dur = max(main_dur, min_safe_dur)
        main_dur = max(0.05, min(main_dur, 120.0))
        jitter_factor = 0.0
        r = style.randomness
        if r > 0:
            jitter_factor = rng.uniform(-r, r)
            main_dur = max(0.05, main_dur * (1.0 + jitter_factor))
        # settle_oscillations deliberately kept at 0 in all presets; the sinusoidal
        # settle path is preserved for future use but not activated by default.
        settle_dur = 0.0
        if style.settle_oscillations > 0:
            settle_dur = 0.12 * style.settle_oscillations
        return cls(
            start=start_slice,
            goal=goal_joints,
            style=style,
            main_duration=main_dur,
            settle_duration=settle_dur,
            duration_jitter=jitter_factor,
            elapsed=0.0,
            _rng=rng,
        )

    @property
    def total_duration(self) -> float:
        return self.main_duration + self.settle_duration

    def sample(self, dt: float) -> tuple[dict[str, float], float]:
        """Advance plan and return (positions for moving joints, phase 0..1 for progress)."""
        self.elapsed += dt
        out: dict[str, float] = {}
        main_end = self.main_duration
        total = self.total_duration

        if self.elapsed >= total:
            for j, g in self.goal.items():
                out[j] = g
            return out, 1.0

        if self.elapsed <= main_end or main_end <= 0:
            p = self.elapsed / main_end if main_end > 1e-6 else 1.0
            p = min(1.0, p)
            u = _blend_u(p, self.style)
            # Per-tick position noise removed: injecting random offsets at 50 Hz
            # causes servo jitter.  Randomness is expressed only as duration_jitter
            # (trajectory-level), which is set once in create().
            for j, g in self.goal.items():
                s = self.start[j]
                out[j] = s + u * (g - s)
            phase = p * (main_end / total) if total > 0 else 1.0
            return out, min(1.0, phase)

        # Settle phase: damped oscillation around goal
        t = self.elapsed - main_end
        phase_main = main_end / total if total > 0 else 1.0
        if self.settle_duration <= 0 or t >= self.settle_duration:
            for j, g in self.goal.items():
                out[j] = g
            return out, 1.0

        damp = math.exp(-t * 10.0)
        osc = damp * math.sin(t * (2 * math.pi * 6.0)) * 0.015
        for j, g in self.goal.items():
            s = self.start[j]
            span = g - s if abs(g - s) > 1e-6 else 1.0
            out[j] = g + osc * span
        settle_phase = t / self.settle_duration
        phase = phase_main + (1.0 - phase_main) * min(1.0, settle_phase)
        return out, min(1.0, phase)
