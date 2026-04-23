"""SecondOrderDynamics — spring-damper filter for biomimetic joint motion.

Models the second-order ODE:

    y'' + 2ζω y' + ω² y = ω² x(t)

where x is the target (input) and y is the smoothed output.

Behaviour by damping ratio ζ:
    ζ = 1.0  critical damping  — smooth approach, no overshoot
    ζ < 1.0  underdamped       — overshoots target then oscillates ("Q弹")
    ζ > 1.0  overdamped        — slow sluggish approach ("犹豫")

Style presets (f Hz, ζ) — see style.py STYLE_PRESETS for the full mapping:
    gentle    : (1.5, 1.0)  smooth, no overshoot
    confident : (2.5, 0.6)  quick, light bounce
    curious   : (1.0, 0.7)  slow, visible overshoot
    bouncy    : (3.0, 0.35) fast, multi-bounce
    hesitant  : (0.8, 1.5)  over-damped, sluggish
    playback  : (5.0, 0.7)  high-tracking + micro-elasticity (stream_frames)
    linear    : (8.0, 1.0)  near-direct pass-through
"""

from __future__ import annotations

from math import pi


class SecondOrderDynamics:
    """Per-joint spring-damper filter.

    Parameters
    ----------
    f : float
        Natural frequency in Hz.  Higher = faster response.
    z : float
        Damping ratio.
    initial : float
        Starting output position (degrees).
    """

    def __init__(self, f: float, z: float, initial: float = 0.0) -> None:
        self._y = initial    # current output position
        self._yd = 0.0       # current output velocity  (deg/s)
        self._w = 2.0 * pi * max(f, 0.01)
        self._z = z

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_params(self, f: float, z: float) -> None:
        """Update frequency and damping without resetting position or velocity."""
        self._w = 2.0 * pi * max(f, 0.01)
        self._z = z

    def sync_position(self, position: float) -> None:
        """Snap output position to *position* while preserving velocity.

        Call this when hardware safety clamping means the motor did not
        actually move as far as the spring commanded, so the filter does
        not race ahead of the real arm.
        """
        self._y = position

    def reset(self, position: float, velocity: float = 0.0) -> None:
        """Hard-reset both position and velocity (breaks continuity — use sparingly)."""
        self._y = position
        self._yd = velocity

    @property
    def position(self) -> float:
        return self._y

    @property
    def velocity(self) -> float:
        return self._yd

    def is_settled(
        self,
        target: float,
        pos_tol: float = 0.3,
        vel_tol: float = 5.0,
    ) -> bool:
        """Return True when output is within *pos_tol*° of *target* AND
        velocity is below *vel_tol* deg/s."""
        return abs(self._y - target) < pos_tol and abs(self._yd) < vel_tol

    def update(self, target: float, dt: float) -> float:
        """Advance one control tick.  Returns the new output position.

        Uses semi-implicit Euler integration with automatic sub-stepping so
        the method stays numerically stable for any (f, dt) combination up to
        f ≈ 50 Hz at typical control rates.
        """
        # Stability condition: ω·sub_dt < 0.4 (conservative)
        n = max(1, int(self._w * dt / 0.4) + 1)
        sub_dt = dt / n
        for _ in range(n):
            acc = (
                self._w * self._w * (target - self._y)
                - 2.0 * self._z * self._w * self._yd
            )
            self._yd += sub_dt * acc
            self._y += sub_dt * self._yd
        return self._y


# ---------------------------------------------------------------------------
# Helpers for velocity-aware frequency capping
# ---------------------------------------------------------------------------


def spring_peak_factor(z: float) -> float:
    """Conservative estimate of peak_velocity / (amplitude × ω) for a unit
    step response.  Used by :func:`cap_spring_f` to guarantee the spring's
    first commanded tick never exceeds the SafetyKernel velocity hard limit.

    Values are intentionally overestimated to provide a safety margin.
    """
    if z >= 1.0:
        return 0.45    # critical or overdamped
    if z >= 0.7:
        return 1.0
    if z >= 0.5:
        return 1.4
    return 2.0          # very underdamped (bouncy)


def cap_spring_f(
    f: float,
    z: float,
    initial_distance: float,
    max_velocity: float,
) -> float:
    """Return an effective frequency ≤ *f* such that the spring's peak
    commanded velocity stays within *max_velocity* for a step of size
    *initial_distance*.

    The damping ratio *z* (overshoot / bounce character) is always
    preserved unchanged — only *f* (speed) is limited.
    """
    if initial_distance < 0.1 or max_velocity <= 0:
        return f
    factor = spring_peak_factor(z)
    # peak_vel ≈ A × ω × factor  ≤  max_velocity
    # → ω_max = max_velocity / (A × factor)
    # → f_max = ω_max / (2π)
    omega_max = max_velocity / (initial_distance * factor)
    f_max = omega_max / (2.0 * pi)
    return min(f, f_max)
