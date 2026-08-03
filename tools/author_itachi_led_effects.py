#!/usr/bin/env python3
"""Author three 3-second Itachi-inspired LED pixel clips.

This is deliberately an authoring utility, not a device-control command.  It
creates safe LEF1 packages in the local expression library; the user can
inspect, edit, and explicitly sync either effect from the LampGo console.

Usage:
    uv run python tools/author_itachi_led_effects.py --install
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from typing import Any

from lampgo.expression_library import save_led_effect
from lampgo.led_effects import compile_led_program, inspect_led_package

WIDTH = 51
HEIGHT = 9

Canvas = list[list[str]]
Point = tuple[int, int]


def _blank() -> Canvas:
    return [["." for _ in range(WIDTH)] for _ in range(HEIGHT)]


def _paint(canvas: Canvas, symbol: str, points: Iterable[Point]) -> None:
    for x, y in points:
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            canvas[y][x] = symbol


def _span(canvas: Canvas, symbol: str, *, y: int, start: int, end: int) -> None:
    _paint(canvas, symbol, ((x, y) for x in range(start, end + 1)))


def _rows(canvas: Canvas) -> list[str]:
    return ["".join(row) for row in canvas]


def _dim(canvas: Canvas) -> Canvas:
    """Reduce the authored reds for a readable final fade, not a hard cut."""
    faded = {"5": "3", "4": "4", "3": "2", "2": "1", "1": "1"}
    return [[faded.get(cell, cell) for cell in row] for row in canvas]


def _draw_eye_outline(canvas: Canvas, level: int, *, center_x: int = 25) -> None:
    """Draw one opening eye around ``center_x`` on the 51-pixel panel."""
    offset = center_x - 25

    if level <= 0:
        _span(canvas, "1", y=4, start=21 + offset, end=29 + offset)
        _span(canvas, "2", y=4, start=23 + offset, end=27 + offset)
        return

    if level == 1:
        segments = ((3, 22, 28), (4, 19, 21), (4, 29, 31), (5, 22, 28))
    elif level == 2:
        segments = (
            (2, 22, 28),
            (3, 19, 21),
            (3, 29, 31),
            (4, 17, 18),
            (4, 32, 33),
            (5, 19, 21),
            (5, 29, 31),
            (6, 22, 28),
        )
    else:
        segments = (
            (1, 22, 28),
            (2, 19, 21),
            (2, 29, 31),
            (3, 17, 18),
            (3, 32, 33),
            (4, 16, 17),
            (4, 33, 34),
            (5, 17, 18),
            (5, 32, 33),
            (6, 19, 21),
            (6, 29, 31),
            (7, 22, 28),
        )
    for y, start, end in segments:
        _span(canvas, "1", y=y, start=start + offset, end=end + offset)


_TOMOE_PHASES: tuple[tuple[tuple[Point, tuple[Point, ...]], ...], ...] = (
    (
        ((25, 2), ((24, 2), (24, 3))),
        ((28, 5), ((28, 4), (27, 4))),
        ((22, 5), ((22, 6), (23, 6))),
    ),
    (
        ((27, 3), ((26, 2), (26, 3))),
        ((26, 6), ((27, 6), (27, 5))),
        ((22, 4), ((22, 5), (23, 5))),
    ),
    (
        ((28, 4), ((27, 3), (27, 4))),
        ((23, 6), ((24, 6), (24, 5))),
        ((23, 2), ((22, 3), (23, 3))),
    ),
)


def _draw_iris(canvas: Canvas, phase: int | None, *, center_x: int = 25) -> None:
    """Draw one Sharingan iris and optionally one tomoe rotation phase."""
    offset = center_x - 25
    iris_points = (
        (24, 2),
        (25, 2),
        (26, 2),
        (23, 3),
        (27, 3),
        (22, 4),
        (28, 4),
        (23, 5),
        (27, 5),
        (24, 6),
        (25, 6),
        (26, 6),
    )

    _paint(
        canvas,
        "2",
        ((x + offset, y) for x, y in iris_points),
    )
    _paint(canvas, "4", ((x + offset, y) for x, y in ((25, 3), (24, 4), (25, 4), (26, 4), (25, 5))))
    _paint(canvas, "5", ((24 + offset, 3),))
    if phase is None:
        return
    for head, tail in _TOMOE_PHASES[phase % len(_TOMOE_PHASES)]:
        _paint(canvas, "2", ((x + offset, y) for x, y in tail))
        _paint(canvas, "3", ((head[0] + offset, head[1]),))


def _sharingan_canvas(*, level: int, phase: int | None = None, droplets: bool = False) -> Canvas:
    canvas = _blank()
    _draw_eye_outline(canvas, level)
    if level >= 3:
        _draw_iris(canvas, phase)
    elif level == 2:
        _paint(canvas, "2", ((24, 4), (25, 4), (26, 4)))
    if droplets:
        _paint(canvas, "2", ((20, 7), (30, 7), (20, 8)))
        _paint(canvas, "3", ((30, 8),))
    return canvas


_DUAL_EYE_CENTERS = (14, 36)


def _dual_sharingan_canvas(*, level: int, phase: int | None = None, droplets: bool = False) -> Canvas:
    """Draw two synchronized Sharingan eyes with a deliberate centre gap."""
    canvas = _blank()
    for center_x in _DUAL_EYE_CENTERS:
        _draw_eye_outline(canvas, level, center_x=center_x)
        if level >= 3:
            _draw_iris(canvas, phase, center_x=center_x)
        elif level == 2:
            _paint(canvas, "2", ((center_x - 1, 4), (center_x, 4), (center_x + 1, 4)))
    if droplets:
        _paint(canvas, "2", ((9, 7), (19, 7), (9, 8), (41, 7), (31, 7), (41, 8)))
        _paint(canvas, "3", ((19, 8), (31, 8)))
    return canvas


def _sharingan_effect() -> dict[str, Any]:
    seed = _blank()
    _paint(seed, "1", ((24, 4), (26, 4)))
    _paint(seed, "2", ((25, 4),))
    final = _sharingan_canvas(level=3, phase=2, droplets=True)
    return {
        "effect_id": "itachi-awaken",
        "label": "写轮眼·同步开眼",
        "role": "accent",
        "default_playback": "once",
        "program": {
            "version": 2,
            "type": "pixel_clip",
            "fps": 10,
            "palette": {
                ".": "#000000",
                "1": "#26000d",
                "2": "#720019",
                "3": "#d10c2d",
                "4": "#2b001f",
                "5": "#ff6370",
            },
            "roles": {"primary": "1", "secondary": "2", "accent": "3"},
            "frames": [
                {"rows": _rows(seed), "ticks": 2},
                {"rows": _rows(_sharingan_canvas(level=0)), "ticks": 2},
                {"rows": _rows(_sharingan_canvas(level=1)), "ticks": 3},
                {"rows": _rows(_sharingan_canvas(level=2)), "ticks": 3},
                {"rows": _rows(_sharingan_canvas(level=3)), "ticks": 3},
                {"rows": _rows(_sharingan_canvas(level=3, phase=0)), "ticks": 2},
                {"rows": _rows(_sharingan_canvas(level=3, phase=1)), "ticks": 2},
                {"rows": _rows(_sharingan_canvas(level=3, phase=2)), "ticks": 2},
                {"rows": _rows(final), "ticks": 8},
                {"rows": _rows(_dim(final)), "ticks": 2},
                {"rows": _rows(_blank()), "ticks": 1},
            ],
        },
    }


def _dual_sharingan_effect() -> dict[str, Any]:
    """Create the compact two-eye counterpart of the existing opening effect."""
    seed = _blank()
    for center_x in _DUAL_EYE_CENTERS:
        _paint(seed, "1", ((center_x - 1, 4), (center_x + 1, 4)))
        _paint(seed, "2", ((center_x, 4),))
    final = _dual_sharingan_canvas(level=3, phase=2, droplets=True)
    return {
        # 11 ASCII characters keeps the LED asset ID well inside the shared
        # C6/S3-safe naming convention; the display name can stay descriptive.
        "effect_id": "itachi-dual",
        "label": "写轮眼·双眼开眼",
        "role": "accent",
        "default_playback": "once",
        "program": {
            "version": 2,
            "type": "pixel_clip",
            "fps": 10,
            "palette": {
                ".": "#000000",
                "1": "#26000d",
                "2": "#720019",
                "3": "#d10c2d",
                "4": "#2b001f",
                "5": "#ff6370",
            },
            "roles": {"primary": "1", "secondary": "2", "accent": "3"},
            "frames": [
                {"rows": _rows(seed), "ticks": 2},
                {"rows": _rows(_dual_sharingan_canvas(level=0)), "ticks": 2},
                {"rows": _rows(_dual_sharingan_canvas(level=1)), "ticks": 3},
                {"rows": _rows(_dual_sharingan_canvas(level=2)), "ticks": 3},
                {"rows": _rows(_dual_sharingan_canvas(level=3)), "ticks": 3},
                {"rows": _rows(_dual_sharingan_canvas(level=3, phase=0)), "ticks": 2},
                {"rows": _rows(_dual_sharingan_canvas(level=3, phase=1)), "ticks": 2},
                {"rows": _rows(_dual_sharingan_canvas(level=3, phase=2)), "ticks": 2},
                {"rows": _rows(final), "ticks": 8},
                {"rows": _rows(_dim(final)), "ticks": 2},
                {"rows": _rows(_blank()), "ticks": 1},
            ],
        },
    }


def _draw_flame(canvas: Canvas, stage: str) -> None:
    if stage == "ember":
        _paint(canvas, "1", ((24, 7), (26, 7), (25, 8)))
        _paint(canvas, "3", ((25, 7),))
        return

    if stage == "small":
        for y, start, end in ((8, 22, 28), (7, 23, 27), (6, 24, 26), (5, 25, 25)):
            _span(canvas, "1", y=y, start=start, end=end)
        _paint(canvas, "2", ((22, 8), (28, 8), (23, 7), (27, 7), (24, 6), (26, 6), (25, 5)))
        _paint(canvas, "3", ((25, 7),))
        return

    if stage == "medium":
        for y, start, end in ((8, 20, 30), (7, 21, 29), (6, 22, 28), (5, 23, 27), (4, 24, 26)):
            _span(canvas, "1", y=y, start=start, end=end)
        _paint(
            canvas,
            "2",
            ((20, 8), (30, 8), (21, 7), (29, 7), (22, 6), (28, 6), (23, 5), (27, 5), (24, 4), (26, 4)),
        )
        _paint(canvas, "3", ((24, 7), (26, 7), (25, 6)))
        _paint(canvas, "4", ((25, 7),))
        return

    if stage != "full":
        raise ValueError(f"unknown flame stage: {stage}")

    for y, start, end in ((8, 19, 31), (7, 20, 30), (6, 21, 29), (5, 22, 28)):
        _span(canvas, "1", y=y, start=start, end=end)
    _paint(canvas, "1", ((23, 4), (25, 4), (27, 4), (23, 3), (26, 3), (24, 2), (27, 2), (24, 1)))
    _paint(
        canvas,
        "2",
        (
            (19, 8),
            (31, 8),
            (20, 7),
            (30, 7),
            (21, 6),
            (29, 6),
            (22, 5),
            (28, 5),
            (23, 4),
            (27, 4),
            (23, 3),
            (26, 3),
            (24, 2),
            (27, 2),
            (24, 1),
        ),
    )
    _paint(canvas, "3", ((23, 7), (25, 6), (27, 7)))
    _paint(canvas, "4", ((25, 7),))


_CROW_POSES: dict[str, tuple[Point, ...]] = {
    "rise": ((-2, 0), (-1, 1), (0, 2), (1, 1), (2, 0)),
    "down": ((-2, 2), (-1, 1), (0, 0), (1, 1), (2, 2)),
    "glide": ((-2, 1), (-1, 1), (0, 1), (1, 1), (2, 1), (0, 2)),
}


def _draw_crow(canvas: Canvas, *, x: int, y: int, pose: str, eye: bool = False) -> None:
    offsets = _CROW_POSES[pose]
    # Near-black purple wings remain legible against the panel's black void;
    # the darker core makes them read as black crows rather than pink birds.
    _paint(canvas, "2", ((x + offset_x, y + offset_y) for offset_x, offset_y in offsets))
    _paint(canvas, "1", ((x, y + 1),))
    if eye:
        _paint(canvas, "3", ((x + 1, y + 1),))


def _amaterasu_canvas(*, stage: str | None, crows: tuple[tuple[int, int, str, bool], ...] = ()) -> Canvas:
    canvas = _blank()
    if stage:
        _draw_flame(canvas, stage)
    for x, y, pose, eye in crows:
        _draw_crow(canvas, x=x, y=y, pose=pose, eye=eye)
    return canvas


def _amaterasu_effect() -> dict[str, Any]:
    return {
        "effect_id": "amaterasu",
        "label": "天照黑焰",
        "role": "accent",
        "default_playback": "once",
        "program": {
            "version": 2,
            "type": "pixel_clip",
            "fps": 10,
            "palette": {
                ".": "#000000",
                "1": "#1a061e",
                "2": "#48203f",
                "3": "#8a1735",
                "4": "#d74458",
            },
            "roles": {"primary": "1", "secondary": "2", "accent": "3"},
            "frames": [
                {"rows": _rows(_amaterasu_canvas(stage="ember")), "ticks": 2},
                {"rows": _rows(_amaterasu_canvas(stage="small")), "ticks": 2},
                {"rows": _rows(_amaterasu_canvas(stage="medium")), "ticks": 2},
                {"rows": _rows(_amaterasu_canvas(stage="full")), "ticks": 5},
                {
                    "rows": _rows(_amaterasu_canvas(stage="full", crows=((31, 2, "rise", True),))),
                    "ticks": 2,
                },
                {
                    "rows": _rows(
                        _amaterasu_canvas(stage="full", crows=((32, 1, "glide", True), (23, 2, "down", False)))
                    ),
                    "ticks": 2,
                },
                {
                    "rows": _rows(
                        _amaterasu_canvas(
                            stage="full",
                            crows=((17, 2, "glide", False), (31, 1, "rise", True), (38, 3, "down", False)),
                        )
                    ),
                    "ticks": 4,
                },
                {
                    "rows": _rows(
                        _amaterasu_canvas(
                            stage="full",
                            crows=((14, 1, "down", False), (34, 2, "glide", True), (42, 1, "rise", False)),
                        )
                    ),
                    "ticks": 3,
                },
                {
                    "rows": _rows(
                        _amaterasu_canvas(stage="medium", crows=((11, 3, "glide", False), (39, 1, "down", True)))
                    ),
                    "ticks": 3,
                },
                {"rows": _rows(_amaterasu_canvas(stage="small")), "ticks": 2},
                {"rows": _rows(_amaterasu_canvas(stage="ember")), "ticks": 2},
                {"rows": _rows(_amaterasu_canvas(stage=None)), "ticks": 1},
            ],
        },
    }


def build_effects() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return all safe LED authoring documents without touching device state."""
    return _sharingan_effect(), _dual_sharingan_effect(), _amaterasu_effect()


def install_effects() -> list[dict[str, Any]]:
    """Save all effects locally; this never uploads or starts an LED effect."""
    return [save_led_effect(effect) for effect in build_effects()]


def _describe(effect: dict[str, Any]) -> str:
    _, package = compile_led_program(effect["program"])
    info = inspect_led_package(package)
    return f"{effect['effect_id']}: {info['bytes']} bytes, {info['unique_frame_count']} unique frames"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        action="store_true",
        help="save the three effects into the local expression library",
    )
    args = parser.parse_args()
    effects = build_effects()
    if args.install:
        for saved in install_effects():
            print(f"installed {saved['effect_id']}: {saved['package']['bytes']} bytes")
        return 0
    for effect in effects:
        print(_describe(effect))
    print("Dry run only. Re-run with --install to save locally; it does not sync hardware.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
