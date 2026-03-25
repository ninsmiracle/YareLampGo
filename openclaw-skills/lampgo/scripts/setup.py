#!/usr/bin/env python3
"""Auto-setup script for lampgo OpenClaw skill.

Called by the OpenClaw agent during first-time configuration.
Detects hardware, writes env vars, checks calibration, starts daemon.

Usage:
    python3 {baseDir}/scripts/setup.py
    python3 {baseDir}/scripts/setup.py --check-only
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cmd(cmd: str) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout.strip()
    except Exception as e:
        return 1, str(e)


def check_lampgo_installed() -> dict:
    code, out = run_cmd("lampgo --help")
    if code == 0:
        return {"installed": True, "method": "lampgo in PATH"}
    code, out = run_cmd("uv run lampgo --help")
    if code == 0:
        return {"installed": True, "method": "uv run lampgo"}
    return {"installed": False, "method": None}


def detect_ports() -> dict:
    code, out = run_cmd("lampgo detect")
    if code != 0:
        code, out = run_cmd("uv run lampgo detect")
    if code == 0:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            pass
    return {"motor_port": None, "led_port": None, "messages": ["Detection failed"]}


def check_calibration(lamp_id: str = "AL01") -> dict:
    paths = [
        Path(f"assets/calibration/{lamp_id}.json"),
        Path.home() / f".cache/huggingface/lerobot/calibration/robots/lelamp_follower/{lamp_id}.json",
    ]
    for p in paths:
        if p.exists():
            return {"found": True, "path": str(p)}
    return {"found": False, "path": None}


def write_env(motor_port: str | None, led_port: str | None, lamp_id: str = "AL01") -> str:
    env_path = Path.home() / ".openclaw" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()

    if motor_port:
        existing["LAMPGO_MOTOR_PORT"] = motor_port
    if led_port:
        existing["LAMPGO_LED_PORT"] = led_port
    existing.setdefault("LAMPGO_LAMP_ID", lamp_id)

    lines = [f"{k}={v}" for k, v in sorted(existing.items())]
    env_path.write_text("\n".join(lines) + "\n")
    return str(env_path)


def start_daemon() -> dict:
    code, out = run_cmd("lampgo status")
    if code == 0:
        try:
            data = json.loads(out)
            if data.get("ok"):
                return {"running": True, "started": False}
        except json.JSONDecodeError:
            pass

    code, out = run_cmd("nohup lampgo run > /tmp/lampgo_daemon.log 2>&1 &")
    import time
    time.sleep(2)

    code, out = run_cmd("lampgo status")
    if code == 0:
        return {"running": True, "started": True}
    return {"running": False, "started": False, "log": "/tmp/lampgo_daemon.log"}


def main():
    check_only = "--check-only" in sys.argv
    result: dict = {"steps": []}

    # Step 1: Check installation
    install = check_lampgo_installed()
    result["steps"].append({"step": "installation", **install})
    if not install["installed"]:
        result["action_required"] = "Install lampgo: cd /path/to/lampgo && uv sync"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Step 2: Detect ports
    ports = detect_ports()
    result["steps"].append({"step": "port_detection", **ports})

    # Step 3: Check calibration
    calib = check_calibration()
    result["steps"].append({"step": "calibration", **calib})

    if check_only:
        result["check_only"] = True
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Step 4: Write env
    if ports.get("motor_port"):
        env_path = write_env(ports["motor_port"], ports.get("led_port"))
        result["steps"].append({"step": "env_written", "path": env_path})

    # Step 5: Start daemon
    daemon = start_daemon()
    result["steps"].append({"step": "daemon", **daemon})

    needs_input = []
    if not ports.get("motor_port"):
        needs_input.append("motor_port (serial port for servos, e.g. /dev/ttyUSB0)")
    if not calib.get("found"):
        needs_input.append("calibration file (AL01.json)")

    if needs_input:
        result["action_required"] = f"Please provide: {', '.join(needs_input)}"
    else:
        result["status"] = "ready"

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
