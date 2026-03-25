"""lampgo CLI — the user-facing command-line interface.

Usage:
    lampgo run [--motor-port PORT] [--led-port PORT]
    lampgo move base_yaw=30 base_pitch=-20
    lampgo play nod
    lampgo skills
    lampgo status
    lampgo estop
    lampgo calibrate --id AL01 --port /dev/ttyUSB0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import structlog

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog="lampgo", description="lampgo — intelligent lamp robot runtime")
    sub = parser.add_subparsers(dest="command")

    # --- run ---
    run_p = sub.add_parser("run", help="Start the lampgo server")
    run_p.add_argument("--motor-port", required=True, help="Serial port for motor bus")
    run_p.add_argument("--led-port", default="", help="Serial port for ESP32 LEDs")
    run_p.add_argument("--lamp-id", default="AL01", help="Lamp identity for calibration")
    run_p.add_argument("--recordings-dir", default="assets/recordings", help="Path to recording CSVs")

    # --- move ---
    move_p = sub.add_parser("move", help="Move joints (e.g. base_yaw=30 base_pitch=-20)")
    move_p.add_argument("joints", nargs="+", help="Joint assignments: name=value")
    move_p.add_argument("--motor-port", required=True)
    move_p.add_argument("--velocity", type=float, default=None)

    # --- play ---
    play_p = sub.add_parser("play", help="Play a recording")
    play_p.add_argument("name", help="Recording name")
    play_p.add_argument("--motor-port", required=True)
    play_p.add_argument("--recordings-dir", default="assets/recordings")
    play_p.add_argument("--fps", type=int, default=0)

    # --- skills ---
    sub.add_parser("skills", help="List available skills")

    # --- calibrate ---
    cal_p = sub.add_parser("calibrate", help="Run interactive motor calibration")
    cal_p.add_argument("--id", required=True, help="Lamp ID")
    cal_p.add_argument("--port", required=True, help="Serial port")

    # --- estop ---
    sub.add_parser("estop", help="Emergency stop")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "move":
        _cmd_move(args)
    elif args.command == "play":
        _cmd_play(args)
    elif args.command == "skills":
        _cmd_skills()
    elif args.command == "calibrate":
        _cmd_calibrate(args)
    elif args.command == "estop":
        print("E-Stop: would send estop to running server (server IPC not yet implemented)")
    else:
        parser.print_help()


def _make_config(args: argparse.Namespace) -> "LampgoConfig":
    from lampgo.core.config import DeviceConfig, LampgoConfig, LEDConfig

    return LampgoConfig(
        device=DeviceConfig(
            motor_port=args.motor_port,
            led_port=getattr(args, "led_port", ""),
            lamp_id=getattr(args, "lamp_id", "AL01"),
        ),
        led=LEDConfig(port=getattr(args, "led_port", "")),
        recordings_dir=Path(getattr(args, "recordings_dir", "assets/recordings")),
    )


def _cmd_run(args: argparse.Namespace) -> None:
    from lampgo.server import run_server

    config = _make_config(args)
    asyncio.run(run_server(config))


def _cmd_move(args: argparse.Namespace) -> None:
    from lampgo.core.config import DeviceConfig, LampgoConfig
    from lampgo.server import LampgoServer

    joints: dict[str, float] = {}
    for pair in args.joints:
        if "=" not in pair:
            print(f"Invalid joint assignment: {pair}  (expected name=value)", file=sys.stderr)
            sys.exit(1)
        name, val = pair.split("=", 1)
        joints[name.strip()] = float(val.strip())

    config = _make_config(args)

    async def _run() -> None:
        server = LampgoServer(config)
        await server.start()
        ctx = server.make_context()
        result = await server.executor.invoke("move_to", ctx, velocity=args.velocity, **joints)
        print(json.dumps({"invocation_id": result.invocation_id, "status": result.status}))
        await server.shutdown()

    asyncio.run(_run())


def _cmd_play(args: argparse.Namespace) -> None:
    from lampgo.server import LampgoServer

    config = _make_config(args)

    async def _run() -> None:
        server = LampgoServer(config)
        await server.start()
        ctx = server.make_context()
        params = {"name": args.name}
        if args.fps:
            params["fps"] = args.fps
        result = await server.executor.invoke("play_recording", ctx, **params)
        print(json.dumps({"invocation_id": result.invocation_id, "status": result.status, "data": result.result}))
        await server.shutdown()

    asyncio.run(_run())


def _cmd_skills() -> None:
    from lampgo.skills.builtin.expression_skills import SetExpressionSkill
    from lampgo.skills.builtin.motion_skills import EStopSkill, MoveToSkill, ReturnSafeSkill
    from lampgo.skills.builtin.playback_skills import PlayRecordingSkill
    from lampgo.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.register(MoveToSkill())
    registry.register(ReturnSafeSkill())
    registry.register(EStopSkill())
    registry.register(PlayRecordingSkill(Path("assets/recordings")))
    registry.register(SetExpressionSkill())

    print(f"{'Skill ID':<20} {'Description'}")
    print("-" * 60)
    for skill in registry.list_skills():
        print(f"{skill.skill_id:<20} {skill.description}")


def _cmd_calibrate(args: argparse.Namespace) -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    config = DeviceConfig(motor_port=args.port, lamp_id=args.id)
    hal = HardwareAbstraction(config)
    hal.connect(calibrate=False)
    try:
        hal.calibrate()
    finally:
        hal.disconnect()


if __name__ == "__main__":
    main()
