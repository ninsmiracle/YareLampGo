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
use local IPC for <100ms latency (Unix socket on POSIX, loopback TCP on Windows).

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


def _configure_windows_console_encoding() -> None:
    """Keep Windows CLI output printable when the active console is not UTF-8."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            continue


def main() -> None:
    _configure_windows_console_encoding()
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
    cal_p.add_argument(
        "--auto-detect",
        "--rescan",
        dest="auto_detect",
        action="store_true",
        help="Ignore the saved motor port and rescan serial ports",
    )

    # --- record ---
    rec_p = sub.add_parser("record", help="Record a teach action (torque off, move arm manually)")
    rec_p.add_argument("name", help="Name for the recording")
    rec_p.add_argument("--motor-port", default=None)
    rec_p.add_argument("--recordings-dir", default=None)
    rec_p.add_argument("--fps", type=int, default=30)

    # --- clear ---
    clear_p = sub.add_parser("clear", help="Stop related processes and release motor torque")
    clear_p.add_argument(
        "--skip-kill",
        action="store_true",
        help="Do not terminate related processes; also skip torque release",
    )
    clear_p.add_argument("--skip-release", action="store_true", help="Do not connect/disconnect motor bus")

    # --- ping ---
    ping_p = sub.add_parser("ping", help="Ping all motor IDs and report status")
    ping_p.add_argument("--port", default=None, help="Serial port (default: config, then auto-detect)")
    ping_p.add_argument(
        "--auto-detect",
        "--rescan",
        dest="auto_detect",
        action="store_true",
        help="Ignore the saved motor port and rescan serial ports",
    )

    # --- setup-motors ---
    setup_p = sub.add_parser("setup-motors", help="Interactively assign Feetech motor IDs")
    setup_p.add_argument("--port", default=None, help="Serial port (default: config, then auto-detect)")
    setup_p.add_argument(
        "--auto-detect",
        "--rescan",
        dest="auto_detect",
        action="store_true",
        help="Ignore the saved motor port and rescan serial ports",
    )

    # --- scan-motors ---
    scan_p = sub.add_parser(
        "scan-motors",
        help="Raw bus scan: probe ID 1-253 with bare pyserial, bypassing lerobot model checks. "
             "Use for hardware diagnosis when calibrate/ping find nothing.",
    )
    scan_p.add_argument("--port", default=None, help="Serial port (default: config, then auto-detect)")
    scan_p.add_argument(
        "--auto-detect",
        "--rescan",
        dest="auto_detect",
        action="store_true",
        help="Ignore the saved motor port and rescan serial ports",
    )
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
        "YareLampGo 常用 CLI\n"
        "===================\n\n"
        "最常用的 4 个命令\n"
        "  uv run lampgo help                 # 打开本速查页\n"
        "  uv run lampgo detect               # 发现串口、摄像头和 ESP32\n"
        "  uv run lampgo calibrate            # 交互式校准 5 个关节\n"
        "  uv run lampgo run --web            # 启动台灯和 Web 控制台\n\n"
        "1) 第一次安装与配置\n"
        "  uv run lampgo onboard              # 配置硬件、LLM、人设和 Codex\n"
        "  uv run lampgo onboard -y --skip persona_memory,codex\n"
        "                                      # 接受默认值，并跳过指定步骤\n\n"
        "2) 发现设备与诊断舵机总线\n"
        "  uv run lampgo detect               # 只读发现串口/摄像头/网络设备\n"
        "  uv run lampgo scan-motors --ids 1-5\n"
        "                                      # 默认自动选择电机 COM 并扫描 V2 的 5 个舵机 ID\n"
        "  uv run lampgo scan-motors --auto-detect --ids 1-5\n"
        "                                      # 忽略旧配置并强制重扫 COM\n"
        "  uv run lampgo scan-motors --port COM5 --ids 1-20\n"
        "                                      # 多串口时显式指定电机适配器\n"
        "  uv run lampgo ping --auto-detect   # 自动重扫并读取舵机状态\n\n"
        "3) 启动、查看状态与退出\n"
        "  uv run lampgo run --web            # 真实硬件；浏览器打开 http://127.0.0.1:8420\n"
        "  uv run lampgo run --web --no-hw    # 无硬件体验 Web/Agent/配置\n"
        "  uv run lampgo run --web --web-port 8421\n"
        "                                      # 修改 Web 端口\n"
        "  uv run lampgo status               # 查询正在运行的 daemon\n"
        "  uv run lampgo skills               # 列出可调用的技能\n"
        "  Ctrl+C                              # 优雅停止 daemon\n\n"
        "4) 调用技能、文本路由与小幅动作\n"
        "  uv run lampgo text \"做个害羞的表情\"\n"
        "                                      # 让意图路由器处理自然语言\n"
        "  uv run lampgo invoke set_expression expression=heart\n"
        "                                      # 按 skill ID 直接调用并传 key=value\n"
        "  uv run lampgo move base_yaw=5 --velocity 20\n"
        "                                      # 校准后再做小角度、低速度测试\n"
        "  uv run lampgo invoke return_safe    # 回到安全位\n\n"
        "5) 舵机编号与校准（会改变硬件状态）\n"
        "  uv run lampgo setup-motors         # 默认自动检测 COM 口\n"
        "  uv run lampgo setup-motors --auto-detect\n"
        "                                      # 忽略旧配置并重新扫描 COM 口\n"
        "  uv run lampgo setup-motors --port COM5\n"
        "                                      # 多串口且无法唯一识别时手动确认\n"
        "  uv run lampgo calibrate --auto-detect --id AL02\n"
        "                                      # 自动选择电机口并从仓库根目录校准\n\n"
        "6) 录制与回放动作\n"
        "  uv run lampgo record my_action --fps 30\n"
        "                                      # 释放扭矩后手动示教，Ctrl+C 结束\n"
        "  uv run lampgo play my_action        # 回放已保存动作\n\n"
        "7) 安全与恢复\n"
        "  uv run lampgo estop                 # 向运行中的 daemon 发送急停\n"
        "  uv run lampgo clear                 # 停止相关进程并尝试释放电机扭矩\n\n"
        "更多参数\n"
        "  uv run lampgo --help\n"
        "  uv run lampgo <command> --help      # 例如 uv run lampgo run --help\n\n"
        "注意：舵机写 ID、校准和首次真实运动前先清空运动范围并准备断开 12V。\n"
        "需要独占串口的调试命令不要和 lampgo run 同时操作同一条电机总线。"
    )


def _find_related_pids() -> list[int]:
    """Find LampGo process ids, excluding the current command and its parent."""
    if os.name == "nt":
        return _find_related_pids_windows()
    return _find_related_pids_posix()


def _find_related_pids_posix() -> list[int]:
    """Find LampGo processes from the POSIX process table."""

    current_pid = os.getpid()
    parent_pid = os.getppid()
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
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


def _find_related_pids_windows() -> list[int]:
    """Find LampGo processes through psutil on Windows."""
    psutil = _load_windows_psutil()
    if psutil is None:
        return []

    current_pid = os.getpid()
    parent_pid = os.getppid()
    markers = (
        "lampgo run",
        "lampgo invoke",
        "lampgo move",
        "lampgo play",
        "lampgo.exe run",
        "lampgo.exe invoke",
        "lampgo.exe move",
        "lampgo.exe play",
    )
    pids: list[int] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(process.info.get("pid", process.pid))
            command = process.info.get("cmdline") or []
            if isinstance(command, str):
                text = command.casefold()
            else:
                text = " ".join(str(item) for item in command).casefold()
        except (AttributeError, KeyError, TypeError, ValueError, psutil.Error):
            continue
        if pid in (current_pid, parent_pid):
            continue
        if any(marker in text for marker in markers):
            pids.append(pid)
    return sorted(set(pids))


def _load_windows_psutil():
    """Return psutil for Windows cleanup, or warn once per attempted operation."""
    try:
        import psutil
    except ImportError:
        print(
            "[warn] Windows process cleanup requires psutil. "
            "Re-run .\\install.ps1 or install the project dependencies.",
            file=sys.stderr,
        )
        return None
    return psutil


def _terminate_pids(pids: list[int]) -> tuple[list[int], list[int]]:
    """Stop related processes and return confirmed-stopped and failed PIDs."""
    if os.name == "nt":
        return _terminate_pids_windows(pids)

    targets = sorted(set(pids))
    failed: set[int] = set()
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            failed.add(pid)

    remaining = _wait_for_posix_pids(
        [pid for pid in targets if pid not in failed],
        timeout_s=1.0,
    )
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            failed.add(pid)

    failed.update(
        _wait_for_posix_pids(
            [pid for pid in remaining if pid not in failed],
            timeout_s=1.0,
        )
    )
    stopped = [pid for pid in targets if pid not in failed]
    return stopped, sorted(failed)


def _wait_for_posix_pids(pids: list[int], timeout_s: float) -> list[int]:
    """Wait for POSIX PIDs to disappear and return any still present."""
    remaining = set(pids)
    deadline = time.monotonic() + timeout_s
    while remaining:
        alive: set[int] = set()
        for pid in remaining:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except OSError:
                alive.add(pid)
            else:
                alive.add(pid)
        remaining = alive
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return sorted(remaining)


def _terminate_pids_windows(pids: list[int]) -> tuple[list[int], list[int]]:
    """Stop Windows processes, waiting after graceful and forced termination."""
    psutil = _load_windows_psutil()
    if psutil is None:
        return [], sorted(set(pids))

    targets = sorted(set(pids))
    failed: set[int] = set()
    processes = []
    for pid in targets:
        try:
            process = psutil.Process(pid)
            process.terminate()
            processes.append(process)
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, OSError):
            failed.add(pid)

    if processes:
        _, alive = psutil.wait_procs(processes, timeout=1.0)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except (psutil.AccessDenied, OSError):
                failed.add(process.pid)
        _, still_alive = psutil.wait_procs(alive, timeout=1.0)
        failed.update(process.pid for process in still_alive)

    stopped = [pid for pid in targets if pid not in failed]
    return stopped, sorted(failed)


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
    port = _resolve_motor_port(args, config)
    if not port:
        print(
            "Error: no unique motor port found. Run `lampgo detect`, "
            "use --auto-detect, or specify --port COM5.",
            file=sys.stderr,
        )
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
    port = _resolve_calibration_port(args, config, interactive=True)
    if not port:
        print(
            "Error: no unique motor port found. Use --port COM5, "
            "--auto-detect, or connect only the motor adapter.",
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
        "Automatic COM detection is enabled by default; use --auto-detect to ignore a saved port.\n"
        "Connect exactly one motor at each prompt; remove 12V before changing motors.\n"
        "Motors with duplicate IDs must not share the bus.\n"
        f"Motor port: {port}\n"
    )

    ordered_names = list(motors)
    for index, name in enumerate(ordered_names, start=1):
        motor = motors[name]
        input(
            f"[{index}/{len(ordered_names)}] Remove 12V, connect only '{name}' "
            f"(target ID {motor.id}), restore power, then press ENTER."
        )

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
        print("  Remove 12V before changing to the next motor.")

    print(
        "All configured motor IDs have been assigned. "
        "Run `lampgo scan-motors --auto-detect --ids 1-5` and `lampgo ping --auto-detect` "
        "to verify the full chain."
    )


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
    from lampgo.core.config import load_config

    cfg = load_config(config_path=getattr(args, "config", None))
    port = _resolve_motor_port(args, cfg)
    if not port:
        print(
            "Error: no unique motor port found. Run `lampgo detect`, "
            "use --auto-detect, or specify --port COM5.",
            file=sys.stderr,
        )
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

    uses_local_motor_port = getattr(config.device, "motor_transport", "serial") == "serial"
    if uses_local_motor_port and not config.device.motor_port and not getattr(args, "no_hw", False):
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

    if uses_local_motor_port and not config.device.motor_port:
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
    process_cleanup_confirmed = False

    if getattr(args, "skip_kill", False):
        lines.append("Skip process cleanup (--skip-kill); torque release is also skipped.")
    elif os.name == "nt" and _load_windows_psutil() is None:
        lines.append("Skipped process cleanup: psutil is unavailable on Windows.")
    else:
        pids = _find_related_pids()
        if not pids:
            lines.append("No related processes found.")
            process_cleanup_confirmed = True
        else:
            stopped, failed = _terminate_pids(pids)
            lines.append(f"Stopped related PIDs: {stopped}")
            if failed:
                lines.append(f"Failed to terminate PIDs: {failed}")
            else:
                process_cleanup_confirmed = True

    if getattr(args, "skip_release", False):
        lines.append("Skip torque release (--skip-release).")
    elif not process_cleanup_confirmed:
        lines.append("Skipped torque release: related processes may still own the motor port.")
    else:
        lines.append(_release_motor_torque(config))

    from lampgo.ipc import cleanup_ipc_endpoint

    try:
        removed = cleanup_ipc_endpoint(config.socket_path)
    except Exception as exc:
        lines.append(f"Failed to clean IPC endpoint {config.socket_path}: {exc}")
    else:
        if removed:
            lines.append(f"Removed stale socket: {config.socket_path}")
        elif os.name == "nt" or str(config.socket_path).startswith("tcp://"):
            lines.append("IPC uses a loopback TCP endpoint; no socket file to remove.")

    print("\n".join(lines))


def _cmd_detect(args: argparse.Namespace) -> None:
    from lampgo.autodetect import detect_ports

    result = detect_ports()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _choose_motor_port(candidates: list[str]) -> str | None:
    """Ask for a safe choice when several serial ports remain ambiguous."""
    if not candidates or not sys.stdin.isatty():
        return None

    print("Automatic detection found multiple possible motor ports:", file=sys.stderr)
    for index, candidate in enumerate(candidates, start=1):
        print(f"  {index}. {candidate}", file=sys.stderr)
    print("Choose the port connected to the Feetech motor adapter.", file=sys.stderr)

    while True:
        try:
            choice = input("Motor port number (ENTER to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return None
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
        print(f"Enter a number from 1 to {len(candidates)}, or press ENTER to cancel.", file=sys.stderr)


def _resolve_motor_port(args: argparse.Namespace, config, *, interactive: bool = False) -> str | None:
    """Resolve a motor port using explicit, saved, or safely detected settings."""
    explicit_port = str(getattr(args, "port", "") or "").strip()
    if explicit_port:
        print(f"Using explicitly selected motor port: {explicit_port}", file=sys.stderr)
        return explicit_port

    configured_port = str(getattr(config.device, "motor_port", "") or "").strip()
    force_detect = bool(getattr(args, "auto_detect", False))
    if configured_port and not force_detect:
        print(
            f"Using configured motor port: {configured_port} "
            "(use --auto-detect to rescan serial ports).",
            file=sys.stderr,
        )
        return configured_port

    from lampgo.autodetect import detect_motor_port

    if force_detect:
        print("Rescanning serial ports for the Feetech motor bus…", file=sys.stderr)
    detected = detect_motor_port()
    for msg in detected.get("messages", []):
        print(f"[detect] {msg}", file=sys.stderr)

    port = str(detected.get("motor_port") or "").strip()
    if port:
        method = str(detected.get("motor_detection") or "")
        if method == "feetech_probe":
            detail = "Feetech response"
        elif method == "single_port_fallback":
            detail = "the only available serial port"
        else:
            detail = "automatic detection"
        print(f"Auto-selected motor port: {port} ({detail}).", file=sys.stderr)
        return port

    candidates = [str(item) for item in detected.get("motor_candidates", []) if str(item).strip()]
    if interactive and candidates:
        selected = _choose_motor_port(candidates)
        if selected:
            print(f"Selected motor port: {selected}", file=sys.stderr)
            return selected
    if candidates:
        print(
            "Automatic detection could not distinguish the motor bus. "
            f"Candidates: {', '.join(candidates)}. Use --port <COMx> after confirming the adapter.",
            file=sys.stderr,
        )
    return None


def _resolve_calibration_port(
    args: argparse.Namespace,
    config,
    *,
    interactive: bool = False,
) -> str | None:
    """Backward-compatible wrapper for calibration/setup motor-port resolution."""
    return _resolve_motor_port(args, config, interactive=interactive)


def _require_calibration_project_root() -> Path:
    """Abort before hardware access unless calibration starts from the repo root."""
    from lampgo.core.config import _find_project_root

    project_root = _find_project_root().resolve()
    current_dir = Path.cwd().resolve()
    is_lampgo_project = (project_root / "pyproject.toml").is_file() and (project_root / "lampgo" / "cli.py").is_file()
    if current_dir != project_root or not is_lampgo_project:
        print(
            "Error: calibration must be run from the LampGo project root; "
            f"current directory is {current_dir}.\n"
            f"Run: cd {project_root} && uv run lampgo calibrate\n"
            "Calibration aborted before accessing hardware.",
            file=sys.stderr,
        )
        sys.exit(2)
    return project_root


def _require_calibration_path_in_project(project_root: Path, calibration_dir: Path, lamp_id: str) -> None:
    """Prevent a calibration profile from being written outside this checkout."""
    target = (calibration_dir / f"{lamp_id}.json").resolve()
    try:
        target.relative_to(project_root)
    except ValueError:
        print(
            "Error: calibration target must stay inside the LampGo project root; "
            f"got {target}.\nCalibration aborted before accessing hardware.",
            file=sys.stderr,
        )
        sys.exit(2)


def _cmd_calibrate(args: argparse.Namespace) -> None:
    from lampgo.core.config import load_config
    from lampgo.core.hal import HardwareAbstraction

    project_root = _require_calibration_project_root()
    config = load_config(config_path=getattr(args, "config", None))
    port = _resolve_calibration_port(args, config, interactive=True)
    lamp_id = args.id or config.device.lamp_id
    _require_calibration_path_in_project(project_root, config.device.calibration_dir, lamp_id)

    if not port:
        print(
            "Error: serial port required. Use --port, set LAMPGO_MOTOR_PORT, or connect hardware for auto-detect.",
            file=sys.stderr,
        )
        sys.exit(1)

    dev_config = config.device.model_copy(update={"motor_port": port, "lamp_id": lamp_id})
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
