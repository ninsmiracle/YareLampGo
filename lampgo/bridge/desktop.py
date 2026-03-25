"""DesktopBridge — abstract InputBackend for PC control (mouse, keyboard, app launch).

The arm can act as a controller: user manipulates the arm, the bridge translates
joint movements into desktop input events (mouse movement, clicks, key presses).

Design: InputBackend is an abstract interface. PyAutoGUI is the default backend,
but can be swapped for HID, platform-specific APIs, or game-specific backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class PermissionLevel(Enum):
    DENIED = "denied"
    READ_ONLY = "read_only"
    FULL = "full"


@dataclass
class DesktopAction:
    """Describes a single desktop action to execute."""

    action_type: str  # "mouse_move", "mouse_click", "key_press", "app_launch"
    params: dict[str, Any]


class InputBackend(ABC):
    """Abstract interface for desktop input injection."""

    @abstractmethod
    def mouse_move(self, dx: int, dy: int) -> None: ...

    @abstractmethod
    def mouse_click(self, button: str = "left") -> None: ...

    @abstractmethod
    def key_press(self, key: str) -> None: ...

    @abstractmethod
    def key_combo(self, *keys: str) -> None: ...

    @abstractmethod
    def app_launch(self, app_name: str) -> bool: ...

    @abstractmethod
    def screenshot(self) -> bytes | None: ...


class PyAutoGUIBackend(InputBackend):
    """Default backend using pyautogui. Requires the [bridge] extra."""

    def __init__(self) -> None:
        self._available = False
        try:
            import pyautogui

            pyautogui.FAILSAFE = True
            self._pyautogui = pyautogui
            self._available = True
        except ImportError:
            logger.warning("desktop.pyautogui_not_installed")

    def mouse_move(self, dx: int, dy: int) -> None:
        if self._available:
            self._pyautogui.moveRel(dx, dy, duration=0.1)

    def mouse_click(self, button: str = "left") -> None:
        if self._available:
            self._pyautogui.click(button=button)

    def key_press(self, key: str) -> None:
        if self._available:
            self._pyautogui.press(key)

    def key_combo(self, *keys: str) -> None:
        if self._available:
            self._pyautogui.hotkey(*keys)

    def app_launch(self, app_name: str) -> bool:
        import subprocess

        try:
            subprocess.Popen(["xdg-open", app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            logger.exception("desktop.app_launch_failed", app=app_name)
            return False

    def screenshot(self) -> bytes | None:
        if not self._available:
            return None
        try:
            import io

            img = self._pyautogui.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None


class StubBackend(InputBackend):
    """No-op backend for testing or when pyautogui is unavailable."""

    def __init__(self) -> None:
        self.action_log: list[str] = []

    def mouse_move(self, dx: int, dy: int) -> None:
        self.action_log.append(f"mouse_move({dx}, {dy})")

    def mouse_click(self, button: str = "left") -> None:
        self.action_log.append(f"mouse_click({button})")

    def key_press(self, key: str) -> None:
        self.action_log.append(f"key_press({key})")

    def key_combo(self, *keys: str) -> None:
        self.action_log.append(f"key_combo({', '.join(keys)})")

    def app_launch(self, app_name: str) -> bool:
        self.action_log.append(f"app_launch({app_name})")
        return True

    def screenshot(self) -> bytes | None:
        return None


class PermissionSystem:
    """Simple permission check for desktop actions.

    M3 scope: allow/deny based on action type.
    """

    def __init__(self, default_level: PermissionLevel = PermissionLevel.DENIED) -> None:
        self._permissions: dict[str, PermissionLevel] = {}
        self._default = default_level

    def grant(self, action_type: str, level: PermissionLevel) -> None:
        self._permissions[action_type] = level
        logger.info("permission.granted", action_type=action_type, level=level.value)

    def revoke(self, action_type: str) -> None:
        self._permissions.pop(action_type, None)
        logger.info("permission.revoked", action_type=action_type)

    def check(self, action_type: str) -> PermissionLevel:
        return self._permissions.get(action_type, self._default)

    def is_allowed(self, action_type: str) -> bool:
        level = self.check(action_type)
        return level in (PermissionLevel.READ_ONLY, PermissionLevel.FULL)


class DesktopBridge:
    """Coordinates desktop actions with permission checks."""

    def __init__(
        self,
        backend: InputBackend | None = None,
        permissions: PermissionSystem | None = None,
    ) -> None:
        self._backend = backend or StubBackend()
        self._permissions = permissions or PermissionSystem()

    def execute_action(self, action: DesktopAction) -> bool:
        if not self._permissions.is_allowed(action.action_type):
            logger.warning("desktop.action_denied", action=action.action_type)
            return False

        try:
            if action.action_type == "mouse_move":
                self._backend.mouse_move(action.params.get("dx", 0), action.params.get("dy", 0))
            elif action.action_type == "mouse_click":
                self._backend.mouse_click(action.params.get("button", "left"))
            elif action.action_type == "key_press":
                self._backend.key_press(action.params.get("key", ""))
            elif action.action_type == "key_combo":
                self._backend.key_combo(*action.params.get("keys", []))
            elif action.action_type == "app_launch":
                return self._backend.app_launch(action.params.get("app", ""))
            else:
                logger.warning("desktop.unknown_action", action=action.action_type)
                return False
            return True
        except Exception:
            logger.exception("desktop.action_failed", action=action.action_type)
            return False

    @property
    def backend(self) -> InputBackend:
        return self._backend

    @property
    def permissions(self) -> PermissionSystem:
        return self._permissions
