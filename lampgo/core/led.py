"""LEDController — ESP32 serial protocol for LED expressions.

Protocol (from legacy lelamp_led.py):
  - "m<N>\n"  — set mode N (0-29)
  - "b<N>\n"  — set brightness N (1-255)
Baud rate: 9600.
"""

from __future__ import annotations

import structlog

from lampgo.core.config import LEDConfig

logger = structlog.get_logger(__name__)

LED_EXPRESSIONS: dict[str, int] = {
    "off": 0,
    "red": 1,
    "green": 2,
    "blue": 3,
    "white": 4,
    "theater": 5,
    "rainbow": 9,
    "smiley": 10,
    "crying": 11,
    "left": 12,
    "right": 13,
    "check": 14,
    "cross": 15,
    "music": 16,
    "blush": 17,
    "angry": 18,
    "surprised": 19,
    "exclaim": 20,
    "question": 21,
    "star": 22,
    "up": 23,
    "down": 24,
    "sleep": 25,
    "thinking": 26,
    "heart": 27,
    "heartbreak": 28,
    "helpless": 29,
}


class LEDController:
    """Manages the ESP32 LED strip via serial."""

    def __init__(self, config: LEDConfig) -> None:
        self._config = config
        self._serial = None
        self._connected = False

    def connect(self) -> None:
        if not self._config.port:
            logger.info("led.disabled (no port configured)")
            return

        import serial

        try:
            self._serial = serial.Serial(self._config.port, self._config.baud_rate, timeout=1)
            self._connected = True
            logger.info("led.connected", port=self._config.port)
        except Exception:
            logger.exception("led.connect_failed")

    def disconnect(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_mode(self, mode: int | str) -> bool:
        """Set LED expression by mode number or name."""
        if isinstance(mode, str):
            resolved = LED_EXPRESSIONS.get(mode.lower().strip())
            if resolved is None:
                logger.warning("led.unknown_mode", mode=mode)
                return False
            mode = resolved

        if not 0 <= mode <= 29:
            logger.warning("led.invalid_mode", mode=mode)
            return False

        return self._send(f"m{mode}\n")

    def set_brightness(self, brightness: int) -> bool:
        """Set LED brightness (1-255)."""
        brightness = max(1, min(255, brightness))
        return self._send(f"b{brightness}\n")

    def off(self) -> bool:
        return self.set_mode(0)

    def _send(self, command: str) -> bool:
        if self._serial is None:
            logger.debug("led.send_skipped (not connected)", cmd=command.strip())
            return False
        try:
            self._serial.write(command.encode())
            self._serial.flush()
            logger.info("led.sent", cmd=command.strip(), port=self._config.port)
            return True
        except Exception:
            logger.exception("led.send_failed", cmd=command.strip())
            self._connected = False
            return False
