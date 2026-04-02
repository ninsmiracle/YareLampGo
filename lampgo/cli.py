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
import os
import signal
import subprocess
import sys
import time
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
    run_p.add_argument("--web", action="store_true", help="Enable web UI (chat interface)")
    run_p.add_argument("--web-port", type=int, default=None, help="Web UI port (default: 8420)")
    run_p.add_argument("--no-home", action="store_true", help="Skip automatic homing on startup")
    run_p.add_argument("--no-hw", action="store_true", help="Skip hardware (motors/LED) — voice & web only")

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
    cal_p.add_argument("--port", default=None, help="Serial port (default: config, then auto-detect)")

    # --- record ---
    rec_p = sub.add_parser("record", help="Record a teach action (torque off, move arm manually)")
    rec_p.add_argument("name", help="Name for the recording")
    rec_p.add_argument("--motor-port", default=None)
    rec_p.add_argument("--recordings-dir", default=None)
    rec_p.add_argument("--fps", type=int, default=30)

    # --- clear ---
    clear_p = sub.add_parser("clear", help="Stop related processes and release motor torque")
    clear_p.add_argument("--skip-kill", action="store_true", help="Do not terminate related processes")
    clear_p.add_argument("--skip-release", action="store_true", help="Do not connect/disconnect motor bus")

    # --- ping ---
    ping_p = sub.add_parser("ping", help="Ping all motor IDs and report status")
    ping_p.add_argument("--port", default=None, help="Serial port (default: config, then auto-detect)")

    # --- help ---
    sub.add_parser("help", help="Show quick manual debugging commands")

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
        "clear": _cmd_clear,
        "ping": _cmd_ping,
        "help": _cmd_help,
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
            "Error: lampgo daemon is not running.\n" "Start it with: lampgo run\n" "Or use --motor-port for standalone mode.",
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


def _build_help_text() -> str:
    return (
        "lampgo 常用手动调试命令\n"
        "========================\n\n"
        "1) 串口和配置\n"
        "  uv run lampgo detect\n"
        "  uv run lampgo skills\n\n"
        "2) 启动与状态\n"
        "  uv run lampgo run\n"
        "  uv run lampgo run --web\n"
        "  打开浏览器访问 http://localhost:8420\n"
        "  uv run lampgo status\n\n"
        "3) 安全控制\n"
        "  uv run lampgo estop\n"
        "  uv run lampgo clear\n\n"
        "4) 动作调试（建议先小角度、低速度）\n"
        "  uv run lampgo move base_yaw=5 --velocity 20\n"
        "  uv run lampgo move base_yaw=0 --velocity 20\n"
        "  uv run lampgo invoke return_safe\n\n"
        "5) 校准\n"
        "  uv run lampgo calibrate --port /dev/tty.usbmodemXXXX --id AL01\n\n"
        "6) 表情与文本路由\n"
        "  uv run lampgo invoke set_expression expression=heart\n"
        '  uv run lampgo text "做个害羞的表情"\n\n'
        "7) 硬件检测（串口 + 摄像头）\n"
        "  uv run lampgo detect\n\n"
        "提示: 推荐用 Ctrl+C 优雅退出 daemon，避免电机保持扭矩锁死。"
    )


def _find_related_pids() -> list[int]:
    """Find lampgo/openclaw related process ids, excluding current process."""
    current_pid = os.getpid()
    parent_pid = os.getppid()
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    markers = (
        "openclaw",
        "lampgo run",
        "lampgo invoke",
        "lampgo move",
        "lampgo play",
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if pid in (current_pid, parent_pid):
            continue
        if any(marker in cmd for marker in markers):
            pids.append(pid)
    return sorted(set(pids))


def _terminate_pids(pids: list[int]) -> tuple[list[int], list[int]]:
    """Try graceful terminate first, then force kill remaining."""
    terminated: list[int] = []
    failed: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append(pid)
        except ProcessLookupError:
            pass
        except Exception:
            failed.append(pid)
    time.sleep(0.2)
    for pid in list(terminated):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except Exception:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except Exception:
            failed.append(pid)
    return terminated, sorted(set(failed))


def _release_motor_torque(config) -> str:
    """Connect and disconnect once to release torque cleanly."""
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    port = config.device.motor_port
    if not port:
        return "Skipped torque release: motor_port not configured."

    try:
        hal = HardwareAbstraction(DeviceConfig(motor_port=port, lamp_id=config.device.lamp_id))
        hal.connect(calibrate=False)
        hal.disconnect()
        return f"Torque release done on {port}."
    except Exception as e:
        return f"Torque release failed on {port}: {e}"


def _cmd_ping(args: argparse.Namespace) -> None:
    """Ping each configured motor ID and report online/offline status."""
    from lampgo.core.config import load_config

    config = load_config(config_path=getattr(args, "config", None))
    port = args.port or config.device.motor_port
    if not port:
        from lampgo.autodetect import detect_ports

        detected = detect_ports()
        port = detected.get("motor_port")
    if not port:
        print("Error: no motor port found. Use --port or configure it.", file=sys.stderr)
        sys.exit(1)

    try:
        from lerobot.motors import Motor, MotorNormMode
        from lerobot.motors.feetech import FeetechMotorsBus
    except ImportError:
        print("Error: lerobot[feetech] not installed.", file=sys.stderr)
        sys.exit(1)

    motors = {name: Motor(mc.id, mc.model, MotorNormMode.DEGREES) for name, mc in config.device.motors.items()}
    bus = FeetechMotorsBus(port=port, motors=motors)
    bus.connect(handshake=False)

    all_ok = True
    for name, m in motors.items():
        model, comm, error = bus.packet_handler.ping(bus.port_handler, m.id)
        if not bus._is_comm_success(comm):
            all_ok = False
            print(f"  ID={m.id:>2} ({name:>15}): ✗ OFFLINE " f"({bus.packet_handler.getTxRxResult(comm)})")
            continue

        if bus._is_error(error):
            all_ok = False
            print(f"  ID={m.id:>2} ({name:>15}): ! STATUS ERROR " f"(model={model}, {bus.packet_handler.getRxPacketError(error)})")
            continue

        print(f"  ID={m.id:>2} ({name:>15}): ✓ online  (model={model})")

    bus.port_handler.closePort()
    sys.exit(0 if all_ok else 1)


def _cmd_help(args: argparse.Namespace) -> None:
    print(_build_help_text())


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
        MoveToSkill,
        ReturnSafeSkill,
        EStopSkill,
        SetExpressionSkill,
        NodSkill,
        HeadShakeSkill,
        LookAtSkill,
        IdleSwaySkill,
        DanceSkill,
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
    if getattr(args, "web", False):
        config.web_enabled = True
    web_port = getattr(args, "web_port", None)
    if web_port is not None:
        config.web.port = web_port
    if getattr(args, "no_home", False):
        config.home_on_start = False
    if getattr(args, "no_hw", False):
        config.no_hw = True
        config.home_on_start = False
    asyncio.run(run_server(config))


def _cmd_clear(args: argparse.Namespace) -> None:
    from lampgo.core.config import load_config

    config = load_config(config_path=getattr(args, "config", None))
    lines: list[str] = []

    if getattr(args, "skip_kill", False):
        lines.append("Skip process cleanup (--skip-kill).")
    else:
        pids = _find_related_pids()
        if not pids:
            lines.append("No related processes found.")
        else:
            terminated, failed = _terminate_pids(pids)
            lines.append(f"Sent terminate to PIDs: {terminated}")
            if failed:
                lines.append(f"Failed to terminate PIDs: {failed}")

    if getattr(args, "skip_release", False):
        lines.append("Skip torque release (--skip-release).")
    else:
        lines.append(_release_motor_torque(config))

    socket_path = Path(config.socket_path)
    if socket_path.exists():
        try:
            socket_path.unlink()
            lines.append(f"Removed stale socket: {socket_path}")
        except Exception as e:
            lines.append(f"Failed to remove socket {socket_path}: {e}")

    print("\n".join(lines))


def _cmd_detect(args: argparse.Namespace) -> None:
    from lampgo.autodetect import detect_ports

    result = detect_ports()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _resolve_calibration_port(args: argparse.Namespace, config) -> str | None:
    """Resolve calibration port from CLI/config, then fall back to auto-detect."""
    port = args.port or config.device.motor_port
    if port:
        return port

    from lampgo.autodetect import detect_ports

    detected = detect_ports()
    for msg in detected.get("messages", []):
        print(f"[detect] {msg}", file=sys.stderr)
    port = detected.get("motor_port")
    if port:
        print(f"Auto-detected motor port: {port}", file=sys.stderr)
    return port


def _cmd_calibrate(args: argparse.Namespace) -> None:
    from lampgo.core.config import DeviceConfig, load_config
    from lampgo.core.hal import HardwareAbstraction

    config = load_config(config_path=getattr(args, "config", None))
    port = _resolve_calibration_port(args, config)
    lamp_id = args.id or config.device.lamp_id

    if not port:
        print(
            "Error: serial port required. Use --port, set LAMPGO_MOTOR_PORT, or connect hardware for auto-detect.",
            file=sys.stderr,
        )
        sys.exit(1)

    dev_config = DeviceConfig(motor_port=port, lamp_id=lamp_id)
    hal = HardwareAbstraction(dev_config)
    try:
        hal.connect(calibrate=False)
        hal.calibrate()
    except Exception as e:
        print(
            f"Calibration failed on {port}: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        print(
            "Hint: ensure no other process is occupying the serial port (try `uv run lampgo clear`) and verify " "motor bus power/cable/ID wiring.",
            file=sys.stderr,
        )
        sys.exit(1)
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

    # User teach-in recordings go to the user/ subdir (gitignored) to keep
    # built-in assets clean. Override with --recordings-dir if needed.
    default_user_dir = Path(config.recordings_dir) / "user"
    recordings_dir = Path(args.recordings_dir) if args.recordings_dir else default_user_dir
    recordings_dir.mkdir(parents=True, exist_ok=True)
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
