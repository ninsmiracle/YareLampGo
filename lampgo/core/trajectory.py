"""Pre-computed trajectory frame generators for lampgo skills.

All parametric and rhythmic motions (nod, headshake, dance, idle_sway, …) should
call one of these helpers to produce a flat list of position frames, then hand the
list to ``MotionRuntime.stream_frames()``.  This approach guarantees:

* The motion planner sees the full trajectory up front — no mid-motion re-planning.
* The control thread plays frames at a fixed FPS via ``clamp_positions`` (position
  limits only, no per-tick velocity clamp that would distort the curve).
* Cancellation is handled by calling ``MotionRuntime.stop_immediate()``, which sets
  the stream's done-event so the skill's ``await _await_done(...)`` returns cleanly.

Motion API usage guide
----------------------
| Motion type             | API to use                          | Examples                  |
|-------------------------|-------------------------------------|---------------------------|
| Point-to-point (single) | ``move_to()``                       | return_safe, look_at      |
| Pre-computed rhythmic   | ``stream_frames()`` / ``play_frames()`` | nod, headshake, dance, idle_sway |
| Real-time reactive      | ``update_target(style="linear")``   | visual-servo face-follow  |

Do **not** use ``update_target`` in a fixed-rate loop to implement scripted motions;
high-frequency target updates rebuild the trajectory every tick, producing the
micro-start/stop stutter that this module is designed to eliminate.
"""

from __future__ import annotations

import math
from typing import Callable

from lampgo.core.style import EASE_FUNCTIONS, ease_out_back


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_ease(fn_name: str, t: float, overshoot: float = 0.10) -> float:
    """Apply a named easing function to normalised time t ∈ [0, 1]."""
    t = max(0.0, min(1.0, t))
    if fn_name == "ease_out_back":
        return ease_out_back(t, overshoot=overshoot)
    return EASE_FUNCTIONS.get(fn_name, EASE_FUNCTIONS["ease_in_out_cubic"])(t)


def _sine_peak_velocity(amplitude: float, period: float) -> float:
    """Return the peak angular velocity (deg/s) of a sinusoidal motion.

    For x(t) = A·sin(2π·t/T), dx/dt|_max = 2π·A/T.
    """
    if period <= 0:
        return float("inf")
    return 2.0 * math.pi * amplitude / period


def _safe_period(amplitude: float, period: float, safety_max_velocity: float) -> float:
    """Lengthen *period* so the peak sine velocity stays within *safety_max_velocity*.

    A 10 % headroom is applied to stay safely below the hard cap even after
    floating-point rounding.
    """
    if amplitude <= 0 or safety_max_velocity <= 0:
        return period
    # peak = 2π·A / T  ≤  0.9 · safety_max_velocity
    # → T  ≥  2π·A / (0.9 · safety_max_velocity)
    min_period = (2.0 * math.pi * amplitude) / (0.9 * safety_max_velocity)
    return max(period, min_period)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_sine_frames(
    base: dict[str, float],
    axes: dict[str, dict],
    duration: float,
    fps: int = 50,
    safety_max_velocity: float = 180.0,
) -> list[dict[str, float]]:
    """Generate a continuous sinusoidal oscillation frame sequence.

    Parameters
    ----------
    base:
        Rest/centre positions for each joint, e.g.
        ``{"base_yaw": 0.0, "base_pitch": -38.0}``.
    axes:
        Per-joint oscillation parameters.  Each key is a joint name; the value
        is a dict with optional keys:

        * ``amplitude`` (float, deg) — half-peak excursion, default 5.0
        * ``period`` (float, s) — full cycle duration, default 4.0
        * ``phase`` (float, rad) — initial phase offset, default 0.0

        Example::

            axes = {
                "base_pitch": {"amplitude": 5.0, "period": 4.0},
                "base_yaw":   {"amplitude": 1.5, "period": 4.0 / 0.7, "phase": 0.0},
            }

    duration:
        Total motion duration in seconds.
    fps:
        Frame rate for the output list (frames are sent to the servo bus at this
        rate via ``stream_frames``).  50 Hz is the default control-loop rate.
    safety_max_velocity:
        Hard velocity cap (deg/s) from ``SafetyConfig.max_velocity``.  Each
        axis's period is automatically extended if needed so no frame-to-frame
        delta would exceed this limit.

    Returns
    -------
    list[dict[str, float]]
        Ready-to-use frame list for ``MotionRuntime.stream_frames(frames, fps)``.
    """
    if fps <= 0:
        fps = 50
    dt = 1.0 / fps
    n_frames = max(1, round(duration * fps))

    # Resolve per-axis parameters and enforce velocity safety
    resolved: list[tuple[str, float, float, float]] = []  # (joint, amp, period, phase)
    for joint, cfg in axes.items():
        amp = float(cfg.get("amplitude", 5.0))
        period = float(cfg.get("period", 4.0))
        phase = float(cfg.get("phase", 0.0))
        period = _safe_period(amp, period, safety_max_velocity)
        resolved.append((joint, amp, period, phase))

    frames: list[dict[str, float]] = []
    for i in range(n_frames):
        t = i * dt
        frame: dict[str, float] = {}
        for joint, amp, period, phase in resolved:
            offset = amp * math.sin(2.0 * math.pi * t / period + phase)
            frame[joint] = base.get(joint, 0.0) + offset
        frames.append(frame)

    # Ensure the last frame snaps back to base (smooth landing)
    if frames:
        frames[-1] = dict(base)

    return frames


def generate_waypoint_frames(
    waypoints: list[tuple[dict[str, float], float]],
    fps: int = 50,
    ease_fn: str = "ease_in_out_cubic",
    ease_overshoot: float = 0.10,
    safety_max_velocity: float = 180.0,
) -> list[dict[str, float]]:
    """Generate a frame sequence by interpolating through a list of keyframes.

    Each segment is eased independently (start → end), so consecutive segments
    join smoothly as long as adjacent target positions are close.  For bouncy
    feel, use ``ease_fn="ease_out_back"`` which overshoots slightly past the
    target before settling.

    Parameters
    ----------
    waypoints:
        List of ``(joints_dict, segment_duration)`` tuples.  The first waypoint
        is the starting pose; each subsequent one is a target with a travel time.

        Example for a single nod::

            [
                ({"base_pitch": 0.0},  0.0),   # start (implicit, duration ignored)
                ({"base_pitch": -15.0}, 0.18),  # dip down
                ({"base_pitch": 4.5},   0.12),  # micro rebound
                ({"base_pitch": 0.0},   0.15),  # return
            ]

    fps:
        Output frame rate (Hz).
    ease_fn:
        Easing function name — one of ``"ease_in_out_cubic"``,
        ``"ease_out_back"``, ``"ease_in_out_quad"``, ``"ease_out_cubic"``,
        ``"ease_in_cubic"``, ``"linear"``.
    ease_overshoot:
        Overshoot factor for ``ease_out_back`` (ignored for other functions).
    safety_max_velocity:
        Hard velocity cap (deg/s).  Segment durations are automatically
        extended so no frame-to-frame delta exceeds this limit.

    Returns
    -------
    list[dict[str, float]]
        Ready-to-use frame list for ``MotionRuntime.stream_frames(frames, fps)``.
    """
    if fps <= 0:
        fps = 50
    dt = 1.0 / fps

    if not waypoints or len(waypoints) < 2:
        if waypoints:
            return [dict(waypoints[0][0])]
        return []

    # Collect all joint names mentioned across all waypoints
    all_joints: set[str] = set()
    for joints, _ in waypoints:
        all_joints.update(joints.keys())

    def _resolve(wp: dict[str, float], reference: dict[str, float]) -> dict[str, float]:
        """Fill missing joints from *reference*."""
        return {j: wp.get(j, reference.get(j, 0.0)) for j in all_joints}

    frames: list[dict[str, float]] = []

    # Determine a safe minimum duration for each segment based on max excursion
    vel_cap = safety_max_velocity * 0.9

    # Ease peak derivative (worst case for each function)
    _PEAK_D: dict[str, float] = {
        "linear": 1.0,
        "ease_in_out_quad": 2.0,
        "ease_out_back": 1.0 + ease_overshoot + 2.0,  # c + 2 where c = 1 + overshoot
    }
    peak_d = _PEAK_D.get(ease_fn, 3.0)  # cubic variants all peak at ~3.0

    prev_joints = _resolve(waypoints[0][0], {})

    for seg_idx in range(1, len(waypoints)):
        target_raw, seg_dur = waypoints[seg_idx]
        target_joints = _resolve(target_raw, prev_joints)

        # Enforce velocity safety: extend duration if the peak eased velocity
        # would exceed the hard cap
        max_dist = max(abs(target_joints[j] - prev_joints.get(j, target_joints[j]))
                       for j in all_joints)
        if max_dist > 1e-6 and vel_cap > 0:
            min_safe_dur = peak_d * max_dist / vel_cap
            seg_dur = max(seg_dur, min_safe_dur)
        seg_dur = max(seg_dur, dt)  # at least one frame

        n_frames = max(1, round(seg_dur * fps))

        ease_fn_callable: Callable[[float], float]
        if ease_fn == "ease_out_back":
            def ease_fn_callable(t: float, _ov: float = ease_overshoot) -> float:
                return ease_out_back(t, overshoot=_ov)
        else:
            ease_fn_callable = EASE_FUNCTIONS.get(ease_fn, EASE_FUNCTIONS["ease_in_out_cubic"])

        for i in range(n_frames):
            # Use i+1 so the last frame exactly reaches the target
            t = (i + 1) / n_frames
            u = ease_fn_callable(t)
            frame: dict[str, float] = {}
            for joint in all_joints:
                s = prev_joints.get(joint, 0.0)
                g = target_joints.get(joint, s)
                frame[joint] = s + u * (g - s)
            frames.append(frame)

        prev_joints = target_joints

    return frames
