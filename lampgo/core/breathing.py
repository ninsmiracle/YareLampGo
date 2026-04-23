"""BreathingGenerator — intermittent postural micro-shifts for biological presence.

Alternates between two phases:

1. **Hold** (5–8 s, random) — complete stillness.  The servo PID rests at zero
   error, producing no audible noise.

2. **Shift** (5–6 s, random) — cosine-eased transition to a new micro-posture.
   Duration chosen so peak velocity < 1 °/s (same regime as a ``return_safe``
   final approach), keeping the motion completely inaudible.

   Peak velocity formula: v_peak = π × A × scale / (2 × T_shift)
   At amplitude=3.0° and T_shift=5 s, base_pitch peaks at ~0.94 °/s.

The visual result is "person occasionally shifting weight in a chair" —
organic presence without continuous servo noise.
"""

from __future__ import annotations

import random
from math import cos, pi


class BreathingGenerator:
    """Generates organic idle micro-motion via intermittent postural shifts.

    Parameters
    ----------
    amplitude : float
        Peak shift magnitude in degrees (before per-joint scaling).
    """

    _AMP_SCALE: dict[str, float] = {
        "base_yaw":    0.35,
        "base_pitch":  1.00,
        "elbow_pitch": 0.80,
        "wrist_roll":  0.25,
        "wrist_pitch": 0.45,
    }

    # Hold: pure stillness. Shift: slow cosine-eased glide.
    _HOLD_RANGE: tuple[float, float] = (5.0, 8.0)
    _SHIFT_RANGE: tuple[float, float] = (5.0, 6.0)

    def __init__(self, amplitude: float = 3.0) -> None:
        self._amplitude = amplitude
        self._rest: dict[str, float] = {}
        self._current_offsets: dict[str, float] = {}
        self._shift_start: dict[str, float] = {}
        self._shift_target: dict[str, float] = {}
        self._holding = True
        self._timer = 0.0
        self._phase_dur = random.uniform(*self._HOLD_RANGE)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_rest(self, positions: dict[str, float]) -> None:
        """Set rest positions; resets to a hold phase with zero offset."""
        self._rest = dict(positions)
        self._current_offsets = {j: 0.0 for j in positions}
        self._shift_start = dict(self._current_offsets)
        self._shift_target = dict(self._current_offsets)
        self._holding = True
        self._timer = 0.0
        self._phase_dur = random.uniform(*self._HOLD_RANGE)

    def sample(self, dt: float) -> dict[str, float]:
        """Advance by *dt* seconds and return absolute target positions."""
        self._timer += dt

        if self._holding:
            if self._timer >= self._phase_dur:
                self._holding = False
                self._timer = 0.0
                self._phase_dur = random.uniform(*self._SHIFT_RANGE)
                self._shift_start = dict(self._current_offsets)
                self._shift_target = self._random_offsets()
        else:
            t = min(1.0, self._timer / self._phase_dur)
            smooth = 0.5 * (1.0 - cos(pi * t))
            for joint in self._current_offsets:
                a = self._shift_start.get(joint, 0.0)
                b = self._shift_target.get(joint, 0.0)
                self._current_offsets[joint] = a + (b - a) * smooth
            if t >= 1.0:
                self._holding = True
                self._timer = 0.0
                self._phase_dur = random.uniform(*self._HOLD_RANGE)

        return {
            joint: rest + self._current_offsets.get(joint, 0.0)
            for joint, rest in self._rest.items()
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _random_offsets(self) -> dict[str, float]:
        """Pick a new random micro-posture within amplitude bounds."""
        return {
            joint: random.uniform(-1.0, 1.0)
                   * self._amplitude
                   * self._AMP_SCALE.get(joint, 0.5)
            for joint in self._rest
        }
