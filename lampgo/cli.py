"""lampgo CLI — the user-facing command-line interface.

Usage:
    lampgo run [--motor-port PORT] [--web]
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
        epilog="Config: ~/.lampgo/config.toml (run `lampgo onboard` to configure; Web UI to edit)",
    )
    parser.add_argument("--config", default=None, help="[deprecated] Ignored; kept for compatibility")
    sub = parser.add_subparsers(dest="command")

    # --- run (start daemon) ---
    run_p = sub.add_parser("run", help="Start the lampgo daemon")
    run_p.add_argument("--motor-port", default=None, help="Serial port for motor bus (overrides config)")
    run_p.add_argument("--led-port", default=None, help="Serial port for ESP32 LEDs (overrides config)")
    run_p.add_argument("--lamp-id", default=None, help="Lamp identity for calibration")
    run_p.add_argument("--recordings-dir", default=None, help="Path to recording CSVs")
    run_p.add_argument("--web", action="store_true", help="Enable web UI (chat interface)")
    run_p.add_argument("--web-port", type=int, default=None, help="Web UI port (default: 8420)")
    run_p.add_argument("--no-home", action="store_true", help="Skip automatic homing on startup")
    run_p.add_argument("--no-hw", action="store_true", help="Skip hardware (motors/LED) — web only")

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

    # --- setup-motors ---
    setup_p = sub.add_parser("setup-motors", help="Interactively assign Feetech motor IDs")
    setup_p.add_argument("--port", default=None, help="Serial port (default: config, then auto-detect)")

    # --- scan-motors ---
    scan_p = sub.add_parser(
        "scan-motors",
        help="Raw bus scan: probe ID 1-253 with bare pyserial, bypassing lerobot model checks. "
             "Use for hardware diagnosis when calibrate/ping find nothing.",
    )
    scan_p.add_argument("--port", default=None, help="Serial port (default: auto-detect)")
    scan_p.add_argument(
        "--baud", type=int, default=1_000_000, help="Baud rate (default: 1000000)"
    )
    scan_p.add_argument(
        "--ids",
        default="1-20",
        help="ID range or list to probe, e.g. '1-20' or '1,3,5' (default: 1-20)",
    )
    scan_p.add_argument(
        "--timeout", type=float, default=0.1, help="Per-ID read timeout in seconds (default: 0.1)"
    )

    # Internal stdio MCP entrypoint. Codex starts this automatically after
    # LampGo registers the integration; users should not need to run it.
    sub.add_parser("mcp-stdio", help="Internal Codex bridge (started automatically)")

    # --- guided onboarding ---
    # Primary name is `onboard`. We also register `install` as a hidden alias so
    # any old docs / muscle memory still works for a while.
    inst_p = sub.add_parser(
        "onboard",
        aliases=["install"],
        help="Guided first-run setup: hardware, LLM, persona, Codex integration",
    )
    inst_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Take defaults for every prompt (useful for scripts / CI).",
    )
    inst_p.add_argument("--yes", "-y", action="store_true", help="Answer yes to confirmations.")
    inst_p.add_argument(
        "--skip",
        default="",
        help="Comma-separated step names to skip "
        "(env_check, audio_tap, hardware, llm, persona_memory, codex).",
    )
    inst_p.add_argument("--motor-port", default=None, help="Preset motor serial port for the hardware step.")
    inst_p.add_argument("--llm-provider", default=None, help="Preset LLM provider for the llm step.")
    inst_p.add_argument("--llm-key", default=None, help="Preset LLM API key for the llm step.")

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
        "setup-motors": _cmd_setup_motors,
        "scan-motors": _cmd_scan_motors,
        "mcp-stdio": _cmd_mcp_stdio,
        "onboard": _cmd_onboard,
        "install": _cmd_onboard,
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


def _build_help_text() -> str:
    return (
        "lampgo 常用手动调试命令\n"
        "========================\n\n"
        "0) 第一次使用\n"
        "  uv run lampgo onboard             # 引导式配置 (硬件/LLM/人设/Codex)\n"
        "  uv run lampgo onboard -y --skip persona_memory,codex\n\n"
        "1) 串口和配置\n"
        "  uv run lampgo detect\n"
        "  uv run lampgo scan-motors --ids 1-20\n"
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
        "  uv run lampgo setup-motors --port /dev/tty.usbmodemXXXX  # 逐颗设置舵机 ID\n"
        "  uv run lampgo calibrate --port /dev/tty.usbmodemXXXX --id AL02\n\n"
        "6) 表情与文本路由\n"
        "  uv run lampgo invoke set_expression expression=heart\n"
        '  uv run lampgo text "做个害羞的表情"\n\n'
        "7) 录制与回放（CSV）\n"
        "  uv run lampgo record my_action\n"
        "  uv run lampgo play my_action\n"
        "  录制按 Ctrl+C 结束，默认保存到 assets/recordings/user/\n\n"
        "8) 硬件检测（串口 + 摄像头）\n"
        "  uv run lampgo detect\n"
        "  uv run lampgo scan-motors --ids 1-20\n\n"
        "提示: 推荐用 Ctrl+C 优雅退出 daemon，避免电机保持扭矩锁死。"
    )


def _find_related_pids() -> list[int]:
    """Find LampGo process ids, excluding the current command and its parent."""
    current_pid = os.getpid()
    parent_pid = os.getppid()
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    markers = (
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
            print(
                f"  ID={m.id:>2} ({name:>15}): ! STATUS ERROR "
                f"(model={model}, {bus.packet_handler.getRxPacketError(error)})"
            )
            continue

        print(f"  ID={m.id:>2} ({name:>15}): ✓ online  (model={model})")

    bus.port_handler.closePort()
    sys.exit(0 if all_ok else 1)

def _cmd_setup_motors(args: argparse.Namespace) -> None:
    """Interactively assign each configured Feetech motor ID and baud rate."""
    from lampgo.core.config import load_config

    config = load_config(config_path=getattr(args, "config", None))
    port = _resolve_calibration_port(args, config)
    if not port:
        print(
            "Error: serial port required. Use --port, set LAMPGO_MOTOR_PORT, or connect hardware for auto-detect.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from lerobot.motors import Motor, MotorNormMode
        from lerobot.motors.feetech import FeetechMotorsBus
    except ImportError:
        print("Error: lerobot[feetech] not installed.", file=sys.stderr)
        sys.exit(1)

    motors = {
        name: Motor(mc.id, mc.model, MotorNormMode.DEGREES)
        for name, mc in config.device.motors.items()
    }
    print(
        "This will assign Feetech motor IDs one at a time.\n"
        "Connect exactly one motor when prompted; motors with duplicate IDs must not share the bus.\n"
        f"Port: {port}\n"
    )

    ordered_names = list(motors)
    for index, name in enumerate(ordered_names, start=1):
        motor = motors[name]
        input(f"[{index}/{len(ordered_names)}] Connect only '{name}' (target ID {motor.id}) and press ENTER.")

        bus = FeetechMotorsBus(port=port, motors={name: motor})
        try:
            bus.connect(handshake=False)
            bus.setup_motor(name)
            print(f"  ✓ '{name}' ID set to {motor.id}")
        except Exception as e:
            print(f"Setup failed for '{name}' on {port}: {type(e).__name__}: {e}", file=sys.stderr)
            print(
                "Hint: connect exactly one STS3215 motor, check power/cable, then rerun this command.",
                file=sys.stderr,
            )
            sys.exit(1)
        finally:
            try:
                bus.port_handler.closePort()
            except Exception:
                pass

    print("All configured motor IDs have been assigned. Run `uv run lampgo ping` to verify the full chain.")


def _cmd_scan_motors(args: argparse.Namespace) -> None:
    """Raw SCS/STS bus scan using bare pyserial — no lerobot, no model checks.

    Sends a PING packet to each requested ID and reports any response. Useful
    for hardware diagnosis when ``calibrate`` / ``ping`` report an empty bus.
    """
    try:
        import serial
    except ImportError:
        print("Error: pyserial not installed. Run: uv add pyserial", file=sys.stderr)
        sys.exit(1)

    # --- resolve port ---
    port = getattr(args, "port", None)
    if not port:
        from lampgo.core.config import load_config
        cfg = load_config(config_path=getattr(args, "config", None))
        port = cfg.device.motor_port
    if not port:
        from lampgo.autodetect import detect_ports
        port = detect_ports().get("motor_port")
    if not port:
        print("Error: no motor port found. Use --port or connect hardware.", file=sys.stderr)
        sys.exit(1)

    # --- parse ID range ---
    ids: list[int] = []
    spec: str = getattr(args, "ids", "1-20") or "1-20"
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            ids.extend(range(int(lo), int(hi) + 1))
        else:
            ids.append(int(part))

    baud: int = getattr(args, "baud", 1_000_000)
    timeout: float = getattr(args, "timeout", 0.05)

    print(f"Scanning port {port} at {baud} baud, IDs {spec} …")
    print(f"(TX echo is stripped automatically; each ID gets {int(timeout * 1000)} ms)\n")

    try:
        ser = serial.Serial(port, baud, timeout=timeout)
    except Exception as e:
        print(f"Error: cannot open {port}: {e}", file=sys.stderr)
        sys.exit(1)

    found: list[tuple[int, int]] = []  # (id, model_number)
    PING_INSTR = 0x01

    for motor_id in ids:
        payload = bytes([motor_id, 2, PING_INSTR])
        checksum = (~sum(payload)) & 0xFF
        packet = b"\xff\xff" + payload + bytes([checksum])

        ser.reset_input_buffer()
        ser.write(packet)
        ser.flush()  # wait for OS to actually transmit before switching to RX

        # Read up to 12 bytes: 6 possible TX echo + 6 status response
        raw = ser.read(12)

        # Scan for a valid status-packet starting with FF FF <id>
        model_num: int | None = None
        for i in range(len(raw) - 5):
            if raw[i] == 0xFF and raw[i + 1] == 0xFF and raw[i + 2] == motor_id:
                # status packet: FF FF ID LEN ERROR CHECKSUM
                # Some buses also include model in extended response — accept any reply
                model_num = 0
                break

        if model_num is not None:
            found.append((motor_id, model_num))
            print(f"  ID {motor_id:>3}: ✓  ONLINE")
        else:
            print(f"  ID {motor_id:>3}:    (no response)")

    ser.close()

    print()
    if found:
        print(f"Found {len(found)} motor(s) responding: IDs {[f[0] for f in found]}")
        sys.exit(0)
    else:
        print(
            "No motors responded.\n"
            "Possible causes:\n"
            "  • 12 V power not reaching servos (check driver board output)\n"
            "  • Bus data line disconnected or damaged\n"
            "  • Motor IDs outside the scanned range (try --ids 1-253)\n"
            "  • Wrong baud rate (Feetech default: 1000000, some units: 115200)\n"
            "  • Half-duplex direction-pin issue on this USB adapter"
        )
        sys.exit(1)

def _cmd_mcp_stdio(args: argparse.Namespace) -> None:
    del args
    from lampgo.mcp_stdio import main as mcp_main

    mcp_main()


def _cmd_onboard(args: argparse.Namespace) -> None:
    """Guided first-run onboarding (hardware, LLM, persona, Codex integration)."""
    # Load config once up front so any ``LAMPGO_*`` env vars / .env overrides
    # the user already exported show up as the defaults inside the installer
    # prompts (via ``personastore.get_overrides_toml()`` after ``load_config``).
    try:
        from lampgo.core.config import load_config  # noqa: F401

        load_config()
    except Exception:
        pass

    from lampgo.installer import run_install

    skip_raw = str(getattr(args, "skip", "") or "")
    skip_steps = [s for s in (chunk.strip() for chunk in skip_raw.split(",")) if s]

    report = run_install(
        non_interactive=bool(getattr(args, "non_interactive", False)),
        assume_yes=bool(getattr(args, "yes", False)),
        skip_steps=skip_steps,
        motor_port=getattr(args, "motor_port", None),
        llm_provider=getattr(args, "llm_provider", None),
        llm_key=getattr(args, "llm_key", None),
    )
    sys.exit(1 if report.errors else 0)


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
    from lampgo.skills.builtin.music_skills import DanceToMusicSkill
    from lampgo.skills.builtin.parametric_skills import HeadShakeSkill, IdleSwaySkill, LookAtSkill, NodSkill
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
        DanceToMusicSkill,
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

    if not config.device.motor_port and not getattr(args, "no_hw", False):
        try:
            from lampgo.autodetect import detect_ports

            detected = detect_ports()
            detected_motor_port = str(detected.get("motor_port") or "").strip()
            if detected_motor_port:
                config.device.motor_port = detected_motor_port
                print(
                    f"[info] auto-detected motor_port={detected_motor_port!r}.",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cli.motor_port_autodetect_failed", error=str(exc))

    if not config.device.motor_port:
        # Degrade to no-hardware mode instead of exiting so the Web UI can still boot
        # and let the user configure a motor port through the settings page.
        print(
            "[warn] motor_port not configured — starting in --no-hw mode.\n"
            "       Configure it via `lampgo onboard`, the Web UI (硬件 tab), "
            "--motor-port, LAMPGO_MOTOR_PORT env var, or ~/.lampgo/config.toml.",
            file=sys.stderr,
        )
        config.no_hw = True
        config.home_on_start = False

    return config


def _cmd_run(args: argparse.Namespace) -> None:
    from lampgo.server import run_server

    config = _load_config_from_args(args)
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
        hal.connect(calibrate=False, configure=False)
        hal.calibrate()
    except Exception as e:
        print(
            f"Calibration failed on {port}: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        print(
            "Hint: ensure no other process is occupying the serial port "
            "(try `uv run lampgo clear`) and verify motor bus power/cable/ID wiring.",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        hal.disconnect()


def _cmd_record(args: argparse.Namespace) -> None:
    import time

    from lampgo.core.config import DeviceConfig, load_config
    from lampgo.core.hal import HardwareAbstraction
    from lampgo.recordings import RECORDING_NAME_ERROR, normalize_recording_name
    from lampgo.skills.recorder import TeachRecorder

    name = normalize_recording_name(args.name)
    if not name:
        print(f"Error: {RECORDING_NAME_ERROR}", file=sys.stderr)
        sys.exit(2)

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

    try:
        # Teach-record mode must release torque, otherwise all joints remain locked.
        hal.disable_torque()
        print(f"Recording '{name}' at {args.fps} FPS. Press Ctrl+C to stop...")
        rec.start()
        try:
            while True:
                rec.tick()
                time.sleep(interval)
        except KeyboardInterrupt:
            pass

        rec.stop()
        path = rec.save(name)
        print(f"Saved {rec.frame_count} frames to {path}")
    finally:
        hal.disconnect()


if __name__ == "__main__":
    main()
