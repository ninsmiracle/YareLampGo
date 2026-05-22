"""LEDController — LED expression protocol.

Protocol (from the ESP32 NeoPixel firmware):
  - "m<N>\n"  — set mode N (0-32)
  - "b<N>\n"  — set brightness N (1-255)

The controller can talk directly to a local USB serial LED controller, or
fall back to the paired lampgo-cam ESP32 firmware's /device/led Wi-Fi endpoint.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from lampgo.core.config import LEDConfig

logger = structlog.get_logger(__name__)

LED_EXPRESSION_CATALOG: tuple[dict[str, Any], ...] = (
    {"mode": 0, "name": "off", "label": "熄灭", "animated": False},
    {"mode": 1, "name": "red", "label": "红色逐圈", "animated": True},
    {"mode": 2, "name": "green", "label": "绿色逐圈", "animated": True},
    {"mode": 3, "name": "blue", "label": "蓝色逐圈", "animated": True},
    {"mode": 4, "name": "white", "label": "白色逐圈", "animated": True},
    {"mode": 5, "name": "theater", "label": "剧场追逐", "animated": True},
    {"mode": 6, "name": "theaterred", "label": "红色剧场", "animated": True},
    {"mode": 7, "name": "theatergreen", "label": "绿色剧场", "animated": True},
    {"mode": 8, "name": "theaterblue", "label": "蓝色剧场", "animated": True},
    {"mode": 9, "name": "rainbow", "label": "彩虹渐变", "animated": True},
    {"mode": 10, "name": "rainbowchase", "label": "彩虹追逐", "animated": True},
    {"mode": 11, "name": "left", "label": "左箭头", "animated": False},
    {"mode": 12, "name": "right", "label": "右箭头", "animated": False},
    {"mode": 13, "name": "up", "label": "上箭头", "animated": False},
    {"mode": 14, "name": "down", "label": "下箭头", "animated": False},
    {"mode": 15, "name": "check", "label": "对号", "animated": False},
    {"mode": 16, "name": "cross", "label": "叉号", "animated": False},
    {"mode": 17, "name": "exclaim", "label": "感叹号", "animated": False},
    {"mode": 18, "name": "question", "label": "问号", "animated": False},
    {"mode": 19, "name": "star", "label": "星星", "animated": False},
    {"mode": 20, "name": "music", "label": "音符跳动", "animated": True},
    {"mode": 21, "name": "smiley", "label": "开心", "animated": False},
    {"mode": 22, "name": "sad", "label": "伤心", "animated": False},
    {"mode": 23, "name": "heart", "label": "心动", "animated": True},
    {"mode": 24, "name": "surprised", "label": "惊讶", "animated": True},
    {"mode": 25, "name": "blush", "label": "害羞", "animated": False},
    {"mode": 26, "name": "angry", "label": "生气", "animated": False},
    {"mode": 27, "name": "thinking", "label": "思考", "animated": True},
    {"mode": 28, "name": "sleep", "label": "睡觉", "animated": True},
    {"mode": 29, "name": "helpless", "label": "无奈", "animated": True},
    {"mode": 30, "name": "cool", "label": "耍酷", "animated": False},
    {"mode": 31, "name": "focused", "label": "专注", "animated": True},
    {"mode": 32, "name": "wink", "label": "眨眼", "animated": True},
)

LED_EXPRESSIONS: dict[str, int] = {str(item["name"]): int(item["mode"]) for item in LED_EXPRESSION_CATALOG}
LED_MODE_NAMES: dict[int, str] = {int(item["mode"]): str(item["name"]) for item in LED_EXPRESSION_CATALOG}


def _normalize_mode_key(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


LED_NAME_INDEX: dict[str, str] = {}
for _item in LED_EXPRESSION_CATALOG:
    _name = str(_item["name"])
    LED_NAME_INDEX[_normalize_mode_key(_name)] = _name


def canonical_expression_name(value: int | str) -> str | None:
    """Resolve a mode number or exact expression name to the expression name."""
    if isinstance(value, int):
        return LED_MODE_NAMES.get(value)

    raw = str(value).strip()
    if not raw:
        return None

    key = _normalize_mode_key(raw)
    candidates = [key]
    if key.startswith("m") and len(key) > 1:
        candidates.append(key[1:])

    for candidate in candidates:
        if candidate.isdigit():
            mode = int(candidate)
            if mode in LED_MODE_NAMES:
                return LED_MODE_NAMES[mode]
        resolved = LED_NAME_INDEX.get(candidate)
        if resolved:
            return resolved
    return None


def resolve_expression_mode(value: int | str) -> int | None:
    """Resolve a mode number or exact expression name to the LED mode number."""
    if isinstance(value, int):
        return value if value in LED_MODE_NAMES else None
    canonical = canonical_expression_name(value)
    return LED_EXPRESSIONS.get(canonical) if canonical else None


def led_expression_catalog() -> list[dict[str, Any]]:
    """Return frontend/API-safe metadata copied from the LED firmware modes."""
    return [
        {
            "mode": int(item["mode"]),
            "name": str(item["name"]),
            "label": str(item.get("label") or item["name"]),
            "animated": bool(item.get("animated", False)),
        }
        for item in LED_EXPRESSION_CATALOG
    ]


class LEDController:
    """Manages LED expressions through serial or the ESP32 Wi-Fi endpoint."""

    def __init__(self, config: LEDConfig, esp32_manager: Any | None = None) -> None:
        self._config = config
        self._esp32_manager = esp32_manager
        self._serial = None
        self._connected = False
        self._remote_last_ok = False

    def bind_esp32_manager(self, esp32_manager: Any | None) -> None:
        self._esp32_manager = esp32_manager

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
        if self._connected:
            return True
        manager = self._esp32_manager
        if manager is None:
            return False
        try:
            return bool(manager.is_online())
        except Exception:
            return bool(self._remote_last_ok)

    def set_mode(self, mode: int | str) -> bool:
        """Set LED expression by mode number or name."""
        if isinstance(mode, str):
            resolved = resolve_expression_mode(mode)
            if resolved is None:
                logger.warning("led.unknown_mode", mode=mode)
                return False
            mode = resolved

        if not 0 <= mode <= 32:
            logger.warning("led.invalid_mode", mode=mode)
            return False

        if self._serial is None:
            return self._send_remote({"mode": mode})
        return self._send(f"m{mode}\n")

    def set_brightness(self, brightness: int) -> bool:
        """Set LED brightness (1-255)."""
        brightness = max(1, min(255, brightness))
        if self._serial is None:
            return self._send_remote({"brightness": brightness})
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
            time.sleep(0.05)
            logger.info("led.sent", cmd=command.strip(), port=self._config.port)
            return True
        except Exception:
            logger.exception("led.send_failed", cmd=command.strip())
            self._connected = False
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
            return False

    def _send_remote(self, payload: dict[str, Any]) -> bool:
        manager = self._esp32_manager
        if manager is None:
            logger.debug("led.remote_send_skipped (no esp32 manager)", payload=payload)
            self._remote_last_ok = False
            return False

        try:
            base_url = manager.get_active_base_url()
        except Exception:
            base_url = None
        if not base_url:
            logger.debug("led.remote_send_skipped (no active esp32)", payload=payload)
            self._remote_last_ok = False
            return False

        try:
            body = manager.with_owner_auth(payload, reason="led") if hasattr(manager, "with_owner_auth") else payload
            import httpx

            resp = httpx.post(f"{base_url}/device/led", json=body, timeout=2.0)
            ok = resp.status_code < 400
            if ok:
                try:
                    data = resp.json()
                    ok = bool(data.get("ok", True))
                except Exception:
                    ok = True
            self._remote_last_ok = ok
            if ok:
                logger.info("led.remote_sent", payload=payload, base_url=base_url)
            else:
                logger.warning(
                    "led.remote_send_failed",
                    payload=payload,
                    base_url=base_url,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
            return ok
        except Exception as exc:
            logger.warning(
                "led.remote_send_failed",
                payload=payload,
                base_url=base_url,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._remote_last_ok = False
            return False
