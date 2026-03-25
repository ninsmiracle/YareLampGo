"""lampgo CLI — the user-facing command-line interface.

Usage:
    lampgo run [--motor-port PORT] [--voice]
    lampgo invoke <skill_id> [key=value ...]
    lampgo text "做个害羞的表情"
    lampgo move base_yaw=30 base_pitch=-20
    lampgo play nod
    lampgo skills
    lampgo status
    lampgo detect
    lampgo estop
    lampgo calibrate
    lampgo record my_action

Commands that talk to the daemon (invoke, text, status, skills, estop)
use Unix socket IPC for <100ms latency.

Commands that need standalone hardware access (move, play, calibrate, record)
try IPC first, then fall back to creating their own server instance.
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
    parser = argparse.ArgumentParser(
        prog="lampgo",
        description="lampgo — intelligent lamp robot runtime",
        epilog="Config: lampgo.toml + .env (see lampgo.toml.example and .env.example)",
    )
    parser.add_argument("--config", default=None, help="Path to lampgo.toml (default: auto-detect)")
    sub = parser.add_subparsers(dest="command")

    # --- run (start daemon) ---
    run_p = sub.add_parser("run", help="Start the lampgo daemon")
    run_p.add_argument("--motor-port", default=None, help="Serial port for motor bus (overrides config)")
    run_p.add_argument("--led-port", default=None, help="Serial port for ESP32 LEDs (overrides config)")
    run_p.add_argument("--lamp-id", default=None, help="Lamp identity for calibration")
    run_p.add_argument("--recordings-dir", default=None, help="Path to recording CSVs")
    run_p.add_argument("--voice", action="store_true", help="Enable voice loop (STT/TTS)")

    # --- invoke (IPC) ---
    inv_p = sub.add_parser("invoke", help="Invoke a skill on the running daemon")
    inv_p.add_argument("skill_id", help="Skill ID to invoke")
    inv_p.add_argument("params", nargs="*", help="Parameters: key=value ...")

    # --- text (IPC) ---
    txt_p = sub.add_parser("text", help="Send free text through the intent router")
    txt_p.add_argument("input", help="Text input (e.g. '做个害羞的表情')")

    # --- status (IPC) ---
    sub.add_parser("status", help="Query daemon status")

    # --- skills (local or IPC) ---
    sub.add_parser("skills", help="List available skills")

    # --- detect ---
    sub.add_parser("detect", help="Auto-detect serial ports")

    # --- estop (IPC) ---
    sub.add_parser("estop", help="Emergency stop (sends to daemon)")

    # --- move (IPC or standalone) ---
    move_p = sub.add_parser("move", help="Move joints (e.g. base_yaw=30 base_pitch=-20)")
    move_p.add_argument("joints", nargs="+", help="Joint assignments: name=value")
    move_p.add_argument("--motor-port", default=None)
    move_p.add_argument("--velocity", type=float, default=None)

    # --- play (IPC or standalone) ---
    play_p = sub.add_parser("play", help="Play a recording")
    play_p.add_argument("name", help="Recording name")
    play_p.add_argument("--motor-port", default=None)
    play_p.add_argument("--recordings-dir", default=None)
    play_p.add_argument("--fps", type=int, default=0)

    # --- calibrate ---
    cal_p = sub.add_parser("calibrate", help="Run interactive motor calibration")
    cal_p.add_argument("--id", default=None, help="Lamp ID (default: from config)")
    cal_p.add_argument("--port", default=None, help="Serial port (default: from config)")

    # --- record ---
    rec_p = sub.add_parser("record", help="Record a teach action (torque off, move arm manually)")
    rec_p.add_argument("name", help="Name for the recording")
    rec_p.add_argument("--motor-port", default=None)
    rec_p.add_argument("--recordings-dir", default=None)
    rec_p.add_argument("--fps", type=int, default=30)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "run": _cmd_run,
        "invoke": _cmd_invoke,
        "text": _cmd_text,
        "status": _cmd_status,
        "skills": _cmd_skills,
        "detect": _cmd_detect,
        "estop": _cmd_estop,
        "move": _cmd_move,
        "play": _cmd_play,
        "calibrate": _cmd_calibrate,
        "record": _cmd_record,
    }
    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


# ---------------------------------------------------------------------------
# IPC helpers
# ---------------------------------------------------------------------------

def _ipc_or_die(request: dict) -> dict:
    """Send IPC request; exit with helpful message if daemon is not running."""
    from lampgo.ipc import ipc_send

    try:
        return ipc_send(request)
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        print(
            "Error: lampgo daemon is not running.\n"
            "Start it with: lampgo run\n"
            "Or use --motor-port for standalone mode.",
            file=sys.stderr,
        )
        sys.exit(1)


def _parse_kv_params(pairs: list[str]) -> dict:
    params: dict = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"Invalid parameter: {pair}  (expected key=value)", file=sys.stderr)
            sys.exit(1)
        key, val = pair.split("=", 1)
        try:
            params[key.strip()] = float(val.strip())
        except ValueError:
            params[key.strip()] = val.strip()
    return params


# ---------------------------------------------------------------------------
# IPC-based commands (talk to running daemon)
# ---------------------------------------------------------------------------

def _cmd_invoke(args: argparse.Namespace) -> None:
    params = _parse_kv_params(args.params) if args.params else {}
    result = _ipc_or_die({"cmd": "invoke", "skill_id": args.skill_id, "params": params})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_text(args: argparse.Namespace) -> None:
    result = _ipc_or_die({"cmd": "text", "input": args.input})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_status(args: argparse.Namespace) -> None:
    result = _ipc_or_die({"cmd": "status"})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_estop(args: argparse.Namespace) -> None:
    result = _ipc_or_die({"cmd": "estop"})
    print("E-STOP sent.")
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Commands that try IPC first, then standalone
# ---------------------------------------------------------------------------

def _try_ipc_invoke(skill_id: str, params: dict) -> bool:
    """Try invoking via IPC. Returns True if successful, False if daemon not running."""
    from lampgo.ipc import ipc_send, is_daemon_running

    if not is_daemon_running():
        return False
    result = ipc_send({"cmd": "invoke", "skill_id": skill_id, "params": params})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return True


def _cmd_move(args: argparse.Namespace) -> None:
    joints = _parse_kv_params(args.joints)
    params = dict(joints)
    if args.velocity is not None:
        params["velocity"] = args.velocity

    if _try_ipc_invoke("move_to", params):
        return

    config = _load_config_from_args(args)
    from lampgo.server import LampgoServer

    async def _run() -> None:
        server = LampgoServer(config)
        await server.start()
        ctx = server.make_context()
        result = await server.executor.invoke("move_to", ctx, **params)
        print(json.dumps({"invocation_id": result.invocation_id, "status": result.status}))
        await server.shutdown()

    asyncio.run(_run())


def _cmd_play(args: argparse.Namespace) -> None:
    params: dict = {"name": args.name}
    if args.fps:
        params["fps"] = args.fps

    if _try_ipc_invoke("play_recording", params):
        return

    config = _load_config_from_args(args)
    from lampgo.server import LampgoServer

    async def _run() -> None:
        server = LampgoServer(config)
        await server.start()
        ctx = server.make_context()
        result = await server.executor.invoke("play_recording", ctx, **params)
        print(json.dumps({"invocation_id": result.invocation_id, "status": result.status, "data": result.result}))
        await server.shutdown()

    asyncio.run(_run())


def _cmd_skills(args: argparse.Namespace) -> None:
    from lampgo.ipc import is_daemon_running

    if is_daemon_running():
        result = _ipc_or_die({"cmd": "skills"})
        skills = result.get("result", {}).get("skills", [])
        print(f"{'Skill ID':<20} {'Description'}")
        print("-" * 60)
        for s in skills:
            print(f"{s['skill_id']:<20} {s['description']}")
        return

    from lampgo.skills.builtin.expression_skills import SetExpressionSkill
    from lampgo.skills.builtin.motion_skills import EStopSkill, MoveToSkill, ReturnSafeSkill
    from lampgo.skills.builtin.parametric_skills import DanceSkill, HeadShakeSkill, IdleSwaySkill, LookAtSkill, NodSkill
    from lampgo.skills.builtin.playback_skills import PlayRecordingSkill
    from lampgo.skills.registry import SkillRegistry

    registry = SkillRegistry()
    for skill_cls in [
        MoveToSkill, ReturnSafeSkill, EStopSkill, SetExpressionSkill,
        NodSkill, HeadShakeSkill, LookAtSkill, IdleSwaySkill, DanceSkill,
    ]:
        registry.register(skill_cls())
    registry.register(PlayRecordingSkill(Path("assets/recordings")))

    print(f"{'Skill ID':<20} {'Description'}")
    print("-" * 60)
    for skill in registry.list_skills():
        print(f"{skill.skill_id:<20} {skill.description}")


# ---------------------------------------------------------------------------
# Server / standalone commands
# ---------------------------------------------------------------------------

def _load_config_from_args(args: argparse.Namespace):
    from lampgo.core.config import load_config

    cli_overrides: dict = {}
    motor_port = getattr(args, "motor_port", None)
    if motor_port:
        cli_overrides["device.motor_port"] = motor_port
    led_port = getattr(args, "led_port", None)
    if led_port:
        cli_overrides["device.led_port"] = led_port
    lamp_id = getattr(args, "lamp_id", None)
    if lamp_id:
        cli_overrides["device.lamp_id"] = lamp_id
    recordings_dir = getattr(args, "recordings_dir", None)
    if recordings_dir:
        cli_overrides["recordings_dir"] = Path(recordings_dir)

    config = load_config(config_path=getattr(args, "config", None), cli_overrides=cli_overrides)

    if not config.device.motor_port:
        print(
            "Error: motor_port not configured.\n"
            "Set it via: --motor-port, LAMPGO_MOTOR_PORT env var, .env file, or lampgo.toml\n"
            "See lampgo.toml.example and .env.example for templates.",
            file=sys.stderr,
        )
        sys.exit(1)

    return config


def _cmd_run(args: argparse.Namespace) -> None:
    from lampgo.server import run_server

    config = _load_config_from_args(args)
    if getattr(args, "voice", False):
        config.voice_enabled = True
    asyncio.run(run_server(config))


def _cmd_detect(args: argparse.Namespace) -> None:
    from lampgo.autodetect import detect_ports

    result = detect_ports()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_calibrate(args: argparse.Namespace) -> None:
    from lampgo.core.config import DeviceConfig, load_config
    from lampgo.core.hal import HardwareAbstraction

    config = load_config(config_path=getattr(args, "config", None))
    port = args.port or config.device.motor_port
    lamp_id = args.id or config.device.lamp_id

    if not port:
        print("Error: serial port required. Use --port or set LAMPGO_MOTOR_PORT.", file=sys.stderr)
        sys.exit(1)

    dev_config = DeviceConfig(motor_port=port, lamp_id=lamp_id)
    hal = HardwareAbstraction(dev_config)
    hal.connect(calibrate=False)
    try:
        hal.calibrate()
    finally:
        hal.disconnect()


def _cmd_record(args: argparse.Namespace) -> None:
    import time

    from lampgo.core.config import DeviceConfig, load_config
    from lampgo.core.hal import HardwareAbstraction
    from lampgo.skills.recorder import TeachRecorder

    config = load_config(config_path=getattr(args, "config", None))
    port = args.motor_port or config.device.motor_port
    if not port:
        print("Error: motor_port required. Use --motor-port or set LAMPGO_MOTOR_PORT.", file=sys.stderr)
        sys.exit(1)

    dev_config = DeviceConfig(motor_port=port)
    hal = HardwareAbstraction(dev_config)
    hal.connect()

    recordings_dir = Path(args.recordings_dir) if args.recordings_dir else Path(config.recordings_dir)
    rec = TeachRecorder(hal, recordings_dir, fps=args.fps)
    interval = 1.0 / args.fps

    print(f"Recording '{args.name}' at {args.fps} FPS. Press Ctrl+C to stop...")
    rec.start()
    try:
        while True:
            rec.tick()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass

    rec.stop()
    path = rec.save(args.name)
    print(f"Saved {rec.frame_count} frames to {path}")
    hal.disconnect()


if __name__ == "__main__":
    main()
