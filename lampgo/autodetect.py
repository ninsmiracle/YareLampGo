"""Serial port auto-detection for lampgo hardware.

Detects Feetech motor bus and ESP32 LED controller by probing available ports.
"""

from __future__ import annotations

import glob
import platform
import re

import structlog

logger = structlog.get_logger(__name__)

FEETECH_BAUD = 1_000_000
ESP32_BAUD = 9600
FEETECH_PING_TIMEOUT = 0.3
ESP32_PROBE_TIMEOUT = 0.5


def _list_serial_ports() -> list[str]:
    """List candidate serial ports on the current platform."""
    system = platform.system()
    if system == "Windows":
        try:
            from serial.tools import list_ports

            ports = set()
            for info in list_ports.comports():
                device = str(getattr(info, "device", "") or "").strip()
                if not device:
                    continue
                if _is_bluetooth_serial_port(info):
                    logger.info("autodetect.skip_bluetooth_serial_port", port=device)
                    continue
                ports.add(device)
        except ImportError:
            logger.warning("autodetect.no_pyserial")
            return []
        except Exception as exc:
            logger.warning("autodetect.windows_port_enumeration_failed", error=str(exc))
            return []
        return sorted(ports, key=_serial_port_sort_key)

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


def _is_bluetooth_serial_port(info) -> bool:
    """Return whether a Windows serial-port record is a Bluetooth virtual port."""
    metadata = " ".join(
        str(getattr(info, field, "") or "")
        for field in ("hwid", "description", "name", "manufacturer", "product")
    ).casefold()
    return "bthenum" in metadata or "bluetooth" in metadata or "蓝牙" in metadata


def _serial_port_sort_key(port: str) -> tuple[str, int, str]:
    """Sort COM2 before COM10 while keeping Unix paths deterministic."""
    match = re.search(r"(\d+)$", port)
    if match is None:
        return (port.casefold(), -1, port.casefold())
    prefix = port[: match.start()].casefold()
    return (prefix, int(match.group(1)), port.casefold())


def _probe_feetech(port: str) -> bool:
    """Try to ping motor IDs 1-6 on a Feetech SCS bus. Returns True if any responds.

    Note: SCS protocol is half-duplex single-wire. Raw pyserial probing may fail
    because direction-pin control (handled by lerobot) is not available here. A
    False return does NOT mean the hardware is absent — it may just mean the USB
    adapter does not expose a direction pin. The caller applies a single-port
    fallback in that case.
    """
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
        for motor_id in range(1, 7):
            payload = bytes([motor_id, 2, 1])  # ID, length=2, instruction=PING
            checksum = (~sum(payload)) & 0xFF
            packet = b"\xff\xff" + payload + bytes([checksum])
            ser.reset_input_buffer()
            ser.write(packet)
            ser.flush()
            # Read up to 12 bytes: 6 possible TX echo + 6 response
            raw = ser.read(12)
            # Scan for a valid status-packet header anywhere in the buffer
            for i in range(len(raw) - 5):
                if raw[i : i + 2] == b"\xff\xff" and raw[i + 2] == motor_id:
                    logger.info(
                        "autodetect.feetech_found", port=port, motor_id=motor_id
                    )
                    return True
        return False
    except Exception:
        return False
    finally:
        ser.close()


def _probe_esp32(port: str) -> bool:
    """Try to communicate with ESP32 LED controller at 9600 baud.

    Sends a status query and waits for any non-empty reply. Returns False if no
    data is received, so a bare USB-serial adapter does not false-positive.
    """
    try:
        import serial
    except ImportError:
        return False

    try:
        ser = serial.Serial(port, ESP32_BAUD, timeout=ESP32_PROBE_TIMEOUT)
    except Exception:
        return False

    try:
        import time
        ser.reset_input_buffer()
        ser.write(b"ping\n")
        time.sleep(0.15)
        data = ser.read(ser.in_waiting or 1)
        return len(data) > 0
    except Exception:
        return False
    finally:
        ser.close()


def _list_camera_names() -> dict[int, str]:
    """Best-effort: get human-readable camera names from the OS."""
    names: dict[int, str] = {}
    if platform.system() != "Darwin":
        return names
    try:
        import json as _json
        import subprocess

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

    import os

    camera_backends: list[int | None] = [None]
    if platform.system() == "Windows" and hasattr(cv2, "CAP_DSHOW"):
        camera_backends.insert(0, cv2.CAP_DSHOW)

    for idx in range(4):
        opened = False
        for backend in camera_backends:
            devnull = os.open(os.devnull, os.O_WRONLY)
            old_stderr = os.dup(2)
            os.dup2(devnull, 2)
            cap = None
            try:
                cap = cv2.VideoCapture(idx, backend) if backend is not None else cv2.VideoCapture(idx)
                opened = cap.isOpened()
            finally:
                if cap is not None:
                    cap.release()
                os.dup2(old_stderr, 2)
                os.close(devnull)
                os.close(old_stderr)
            if opened:
                break
        if opened:
            name = cam_names.get(idx, "")
            label = f"{idx} ({name})" if name else str(idx)
            found.append(f"Camera port {label}")
            logger.info("autodetect.camera_found", port=str(idx), name=name)
            if recommended is None:
                recommended = str(idx)

    return recommended, found


def _detect_microphones() -> tuple[str | None, list[str]]:
    """List available input audio devices and recommend one."""
    try:
        import sounddevice as sd
    except ImportError:
        return None, ["Microphone detection skipped: sounddevice not installed."]

    messages: list[str] = []
    recommended: str | None = None
    try:
        devices = sd.query_devices()
        default_input = sd.default.device[0]
    except Exception as e:
        return None, [f"Microphone detection failed: {e}"]

    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            marker = " (default)" if i == default_input else ""
            messages.append(f"  Mic {i}: {dev['name']}{marker}")
            if recommended is None:
                recommended = str(i)

    if not messages:
        messages.append("No microphone found.")
    else:
        messages.insert(0, "Available microphones:")

    return recommended, messages


def _detect_serial_ports() -> dict:
    """Detect serial devices and classify the motor/LED candidates."""
    ports = _list_serial_ports()
    messages: list[str] = []
    motor_port: str | None = None
    led_port: str | None = None
    motor_candidates: list[str] = []
    motor_detection = "none"

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
                motor_candidates = [port]
                motor_detection = "feetech_probe"

        if motor_port is None:
            # SCS half-duplex probing often fails without direction-pin support;
            # fall back gracefully before probing for the LED controller so we
            # don't accidentally assign the only port to LED instead of motors.
            if len(ports) == 1:
                motor_port = ports[0]
                motor_candidates = [motor_port]
                motor_detection = "single_port_fallback"
                messages.append(
                    f"Motor bus auto-probe inconclusive (expected for SCS half-duplex). "
                    f"Only one port available — assuming motor bus: {motor_port}"
                )
            else:
                motor_candidates = list(ports)
                motor_detection = "ambiguous"
                messages.append(
                    "Motor bus not detected automatically. Candidate ports require confirmation: "
                    f"{motor_candidates}"
                )

        remaining = [p for p in ports if p != motor_port]
        for port in remaining:
            if led_port is not None:
                break
            logger.info("autodetect.probing_esp32", port=port)
            if _probe_esp32(port):
                led_port = port
                messages.append(f"LED controller detected: {port}")

        if led_port is None and remaining:
            messages.append(f"LED controller not detected. Candidate ports: {remaining}")

    return {
        "motor_port": motor_port,
        "motor_candidates": motor_candidates,
        "motor_detection": motor_detection,
        "led_port": led_port,
        "all_ports": ports if ports else [],
        "messages": messages,
    }


def detect_motor_port() -> dict:
    """Detect only serial ports needed for motor setup and calibration."""
    return _detect_serial_ports()


def detect_ports() -> dict:
    """Auto-detect motor bus, LED controller, USB camera, and microphones.

    Returns:
        {
            "motor_port": "/dev/ttyUSB0" or None,
            "motor_candidates": [...],
            "motor_detection": "feetech_probe|single_port_fallback|ambiguous|none",
            "led_port": "/dev/ttyUSB1" or None,
            "camera_port": "0" or None,
            "mic_device": "2" or None,
            "all_ports": [...],
            "messages": ["..."]
        }
    """
    detected = _detect_serial_ports()
    messages = list(detected["messages"])

    camera_port, cam_msgs = _detect_camera()
    if cam_msgs:
        messages.extend(cam_msgs)
    elif camera_port is None:
        messages.append("No camera detected. Check USB connection or install opencv-python.")

    mic_device, mic_msgs = _detect_microphones()
    messages.extend(mic_msgs)

    detected.update(
        {
            "camera_port": camera_port,
            "mic_device": mic_device,
            "messages": messages,
        }
    )
    return detected
