"""Listen before USB bus access; this detects contention, not an ownership lease."""

import time


def require_quiet_serial(serial_port, seconds: float = 0.2) -> None:
    data = bytearray()
    deadline = time.monotonic() + seconds
    old_timeout = serial_port.timeout
    serial_port.timeout = 0.01
    try:
        while time.monotonic() < deadline:
            data.extend(serial_port.read(max(1, serial_port.in_waiting)))
            if len(data) >= 128:
                break
    finally:
        serial_port.timeout = old_timeout
    if data:
        raise RuntimeError(
            "串口在未发送命令时已有数据：可能有 P4 正在轮询，或选中了调试串口。"
            "已停止 USB 舵机操作。请只保留一个舵机主控；P4 用户请使用网页无线维护。"
            "这不是舵机型号或传感器故障。"
        )


def require_quiet_port(port: str) -> None:
    import serial

    with serial.Serial(port, baudrate=1_000_000, timeout=0.01) as connection:
        require_quiet_serial(connection)


def has_ping_reply(raw: bytes, motor_id: int, request: bytes) -> bool:
    """A TX echo or a foreign READ request is not a servo ping response."""
    for offset in range(len(raw) - 5):
        packet = raw[offset : offset + 6]
        if packet != request and packet[:4] == bytes((255, 255, motor_id, 2)) and sum(packet[2:]) & 255 == 255:
            return True
    return False
