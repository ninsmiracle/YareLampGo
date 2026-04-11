"""BreathingGenerator — low-frequency idle micro-motion for biological presence.

When the arm is idle (no active MOVE_TO or STREAM_FRAMES command) the
breathing generator provides slowly-oscillating target positions for the
spring bank.  Each joint oscillates at a slightly different frequency with a
unique phase offset so the combined motion looks organic rather than mechanical.

Typical usage
-------------
    gen = BreathingGenerator(amplitude=0.8)
    gen.set_rest(current_joint_positions)   # call when entering idle
    while idle:
        targets = gen.sample(dt)            # dict[joint, float] absolute targets
        # feed targets into spring bank …
"""

from __future__ import annotations

from math import pi, sin


class BreathingGenerator:
    """Generates organic idle micro-motion for all resting joints.

    Parameters
    ----------
    amplitude : float
        Peak oscillation amplitude in degrees.  0.8° is subtle but
        noticeable; reduce to 0.3° for near-imperceptible presence.
    """

    # Per-joint natural breathing frequencies (Hz).
    # Slight mismatches between joints create a Lissajous-like organic feel.
    _FREQS: dict[str, float] = {
        "base_yaw":    0.08,
        "base_pitch":  0.11,
        "elbow_pitch": 0.09,
        "wrist_roll":  0.07,
        "wrist_pitch": 0.10,
    }

    # Phase offsets (radians) so joints start at different points in
    # their cycle — avoids all joints moving in lockstep.
    _PHASES: dict[str, float] = {
        "base_yaw":    0.00,
        "base_pitch":  1.26,   # ≈ 72°
        "elbow_pitch": 2.51,   # ≈ 144°
        "wrist_roll":  0.63,   # ≈ 36°
        "wrist_pitch": 1.88,   # ≈ 108°
    }

    def __init__(self, amplitude: float = 0.8) -> None:
        self._amplitude = amplitude
        self._rest: dict[str, float] = {}
        self._t = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_rest(self, positions: dict[str, float]) -> None:
        """Set the base (rest) positions around which breathing oscillates.

        Should be called whenever the arm enters the idle state so that
        breathing starts from wherever the arm currently is.  The
        internal time is preserved so the motion has no discontinuity.
        """
        self._rest = dict(positions)

    def sample(self, dt: float) -> dict[str, float]:
        """Advance by *dt* seconds and return absolute target positions.

        Returns a dict mapping each resting joint to its current
        breathing target (rest position + sinusoidal offset).
        """
        self._t += dt
        result: dict[str, float] = {}
        for joint, rest in self._rest.items():
            freq = self._FREQS.get(joint, 0.1)
            phase = self._PHASES.get(joint, 0.0)
            offset = self._amplitude * sin(2.0 * pi * freq * self._t + phase)
            result[joint] = rest + offset
        return result
