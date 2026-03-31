"""Serial port auto-detection for lampgo hardware.

Detects Feetech motor bus and ESP32 LED controller by probing available ports.
"""

from __future__ import annotations

import glob
import platform

import structlog

logger = structlog.get_logger(__name__)

FEETECH_BAUD = 1_000_000
ESP32_BAUD = 9600
FEETECH_PING_TIMEOUT = 0.3
ESP32_PROBE_TIMEOUT = 0.5


def _list_serial_ports() -> list[str]:
    """List candidate serial ports on the current platform."""
    system = platform.system()
    patterns: list[str] = []
    if system == "Linux":
        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]
    elif system == "Darwin":
        patterns = ["/dev/tty.usbmodem*", "/dev/tty.usbserial*"]
    else:
        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]

    ports: list[str] = []
    for pattern in patterns:
        ports.extend(sorted(glob.glob(pattern)))
    return ports


def _probe_feetech(port: str) -> bool:
    """Try to ping motor ID 1 on a Feetech bus. Returns True if it responds."""
    try:
        import serial
    except ImportError:
        logger.warning("autodetect.no_pyserial")
        return False

    try:
        ser = serial.Serial(port, FEETECH_BAUD, timeout=FEETECH_PING_TIMEOUT)
    except Exception:
        return False

    try:
        motor_id = 1
        payload = bytes([motor_id, 2, 1])  # ID, length=2, instruction=PING
        checksum = (~sum(payload)) & 0xFF
        packet = b"\xff\xff" + payload + bytes([checksum])
        ser.reset_input_buffer()
        ser.write(packet)
        response = ser.read(6)
        if len(response) >= 6 and response[0:2] == b"\xff\xff":
            return True
        return False
    except Exception:
        return False
    finally:
        ser.close()


def _probe_esp32(port: str) -> bool:
    """Try to communicate with ESP32 LED controller at 9600 baud."""
    try:
        import serial
    except ImportError:
        return False

    try:
        ser = serial.Serial(port, ESP32_BAUD, timeout=ESP32_PROBE_TIMEOUT)
    except Exception:
        return False

    try:
        ser.write(b"m0\n")
        import time
        time.sleep(0.1)
        return True
    except Exception:
        return False
    finally:
        ser.close()


def _list_camera_names() -> dict[int, str]:
    """Best-effort: get human-readable camera names from the OS."""
    names: dict[int, str] = {}
    try:
        import subprocess, json as _json
        proc = subprocess.run(
            ["system_profiler", "SPCameraDataType", "-json"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if proc.returncode == 0:
            data = _json.loads(proc.stdout)
            for i, cam in enumerate(data.get("SPCameraDataType", [])):
                names[i] = cam.get("_name", f"camera_{i}")
    except Exception:
        pass
    return names


def _detect_camera() -> tuple[str | None, list[str]]:
    """Probe camera indices 0..3 and return (recommended_port, info_messages)."""
    try:
        import cv2
    except ImportError:
        logger.info("autodetect.camera_skip", reason="opencv_not_installed")
        return None, ["Camera detection skipped: opencv-python not installed."]

    cam_names = _list_camera_names()
    found: list[str] = []
    recommended: str | None = None

    import os, sys
    for idx in range(4):
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        try:
            cap = cv2.VideoCapture(idx)
            opened = cap.isOpened()
            cap.release()
        finally:
            os.dup2(old_stderr, 2)
            os.close(devnull)
            os.close(old_stderr)
        if opened:
            name = cam_names.get(idx, "")
            label = f"{idx} ({name})" if name else str(idx)
            found.append(f"Camera port {label}")
            logger.info("autodetect.camera_found", port=str(idx), name=name)
            if recommended is None:
                recommended = str(idx)

    return recommended, found


def detect_ports() -> dict:
    """Auto-detect motor bus, LED controller, and USB camera.

    Returns:
        {
            "motor_port": "/dev/ttyUSB0" or None,
            "led_port": "/dev/ttyUSB1" or None,
            "camera_port": "0" or None,
            "all_ports": [...],
            "messages": ["..."]
        }
    """
    ports = _list_serial_ports()
    messages: list[str] = []
    motor_port: str | None = None
    led_port: str | None = None

    if not ports:
        messages.append("No serial ports found. Is the hardware connected?")
    else:
        messages.append(f"Found {len(ports)} serial port(s): {ports}")

        for port in ports:
            if motor_port is not None:
                break
            logger.info("autodetect.probing_feetech", port=port)
            if _probe_feetech(port):
                motor_port = port
                messages.append(f"Motor bus detected: {port}")

        remaining = [p for p in ports if p != motor_port]
        for port in remaining:
            if led_port is not None:
                break
            logger.info("autodetect.probing_esp32", port=port)
            if _probe_esp32(port):
                led_port = port
                messages.append(f"LED controller detected: {port}")

        if motor_port is None:
            messages.append("Motor bus not detected. Check connection and power.")
            if len(ports) == 1:
                motor_port = ports[0]
                messages.append(f"Only one port found, assuming motor bus: {motor_port}")
        if led_port is None and len(remaining) > 0:
            messages.append(f"LED controller not detected. Candidate ports: {remaining}")

    camera_port, cam_msgs = _detect_camera()
    if cam_msgs:
        messages.extend(cam_msgs)
    elif camera_port is None:
        messages.append("No camera detected. Check USB connection or install opencv-python.")

    return {
        "motor_port": motor_port,
        "led_port": led_port,
        "camera_port": camera_port,
        "all_ports": ports if ports else [],
        "messages": messages,
    }


def write_env_file(
    motor_port: str | None = None,
    led_port: str | None = None,
    lamp_id: str = "AL01",
    env_path: str = "~/.openclaw/.env",
) -> str:
    """Write detected ports to an env file (for OpenClaw setup)."""
    from pathlib import Path

    path = Path(env_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
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
    path.write_text("\n".join(lines) + "\n")
    return str(path)
