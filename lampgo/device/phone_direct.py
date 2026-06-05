"""Fast ADB-only phone controls for simple Android tasks."""

from __future__ import annotations

import base64
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

from lampgo.core.config import PhoneAgentConfig
from lampgo.device.phone_paths import find_adb
from lampgo.vendor.open_autoglm.phone_agent.config.apps import APP_PACKAGES


_PACKAGE_RE = re.compile(r"\b[a-zA-Z][\w]*(?:\.[a-zA-Z_][\w]*){1,}\b")
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_SENSITIVE_DIRECT_RE = re.compile(
    r"(付款|支付|下单|购买|转账|删除|注销|发送|提交|确认订单|confirm|send|submit|delete|pay|purchase)",
    re.IGNORECASE,
)
_FOLLOWUP_VERB_RE = re.compile(
    r"(搜索|搜一下|查询|查找|输入|填写|点击|点按|选择|发送|提交|滑动|浏览|登录|注册|search|type|input|tap|click|swipe)",
    re.IGNORECASE,
)

_APP_ALIASES: dict[str, str] = {
    "设置": "Settings",
    "系统设置": "Settings",
    "手机设置": "Settings",
    "安卓设置": "Settings",
    "浏览器": "Chrome",
    "谷歌浏览器": "Chrome",
    "日历": "Google Calendar",
    "联系人": "Contacts",
    "文件": "Files",
    "文件管理": "Files",
    "文件管理器": "Files",
    "应用商店": "Google Play Store",
    "play商店": "Google Play Store",
    "录音机": "AudioRecorder",
    "时钟": "Clock",
    "相机": "com.oplus.camera",
    "手机相机": "com.oplus.camera",
    "自拍": "com.oplus.camera",
    "支付宝": "com.eg.android.AlipayGphone",
    "lampgo camera": "com.lampgo.camera",
    "Lampgo Camera": "com.lampgo.camera",
    "LampgoCamera": "com.lampgo.camera",
}


@dataclass
class DirectPhoneCommand:
    kind: str
    value: str = ""
    x: int | None = None
    y: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DirectPhoneResult:
    ok: bool
    status: str
    action: str
    duration_s: float
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "action": self.action,
            "duration_s": self.duration_s,
            "message": self.message,
            "data": self.data,
            "error": self.error,
        }


def plan_direct_phone_task(task: str, *, allow_sensitive: bool = False) -> DirectPhoneCommand | None:
    """Return a deterministic ADB command for simple tasks, else ``None``.

    This parser is intentionally conservative. Multi-step or ambiguous phone
    tasks still go through Open-AutoGLM, where the model can reason over the UI.
    """
    text = _normalize_task_text(task)
    if not text:
        return None
    if not allow_sensitive and _SENSITIVE_DIRECT_RE.search(text):
        return None

    command = _parse_launch(text)
    if command is not None:
        return command

    command = _parse_key_or_motion(text)
    if command is not None:
        return command

    command = _parse_tap_coordinates(text)
    if command is not None:
        return command

    command = _parse_tap_text(text)
    if command is not None:
        return command

    command = _parse_type_text(text)
    if command is not None:
        return command

    return None


def run_direct_phone_task(
    task: str,
    config: PhoneAgentConfig,
    *,
    device_id: str = "",
    allow_sensitive: bool = False,
) -> DirectPhoneResult | None:
    command = plan_direct_phone_task(task, allow_sensitive=allow_sensitive)
    if command is None:
        return None
    controller = DirectPhoneController(config, device_id=device_id)
    return controller.execute(command, allow_sensitive=allow_sensitive)


class DirectPhoneController:
    """Small deterministic Android controller backed by ``adb shell``."""

    def __init__(self, config: PhoneAgentConfig, *, device_id: str = "") -> None:
        self.config = config
        self.device_id = (device_id or config.device_id).strip()
        self.adb = find_adb(config.adb_path)

    def execute(self, command: DirectPhoneCommand, *, allow_sensitive: bool = False) -> DirectPhoneResult:
        started = time.monotonic()
        if not self.adb:
            return self._result(False, "adb_missing", command, started, error="adb not found")
        if self.config.device_type.strip().lower() != "adb":
            return self._result(
                False,
                "unsupported_device_type",
                command,
                started,
                error=f"direct phone control only supports adb, got {self.config.device_type}",
            )

        try:
            if command.kind == "launch":
                return self._launch(command, started)
            if command.kind == "back":
                return self._keyevent(command, started, "KEYCODE_BACK")
            if command.kind == "home":
                return self._keyevent(command, started, "KEYCODE_HOME")
            if command.kind == "enter":
                return self._keyevent(command, started, "KEYCODE_ENTER")
            if command.kind == "tap":
                return self._tap(command, started)
            if command.kind == "tap_text":
                return self._tap_text(command, started)
            if command.kind == "type":
                if not allow_sensitive and _SENSITIVE_DIRECT_RE.search(command.value):
                    return self._result(False, "sensitive_blocked", command, started, error="sensitive direct input blocked")
                return self._type_text(command, started)
            if command.kind == "swipe":
                return self._swipe(command, started)
            return self._result(False, "unknown_action", command, started, error=f"unknown direct action: {command.kind}")
        except Exception as exc:  # noqa: BLE001
            return self._result(False, "error", command, started, error=str(exc))

    def _launch(self, command: DirectPhoneCommand, started: float) -> DirectPhoneResult:
        app_name = command.value
        package = _resolve_package(app_name)
        if not package:
            return self._result(
                False,
                "app_not_mapped",
                command,
                started,
                error=f"app is not in direct launch map: {app_name}",
            )

        installed = self._run(["shell", "pm", "path", package], timeout=8)
        if installed.returncode != 0 or not installed.stdout.strip():
            return self._result(
                False,
                "app_not_installed",
                command,
                started,
                data={"app": app_name, "package": package},
                error=(installed.stderr or installed.stdout).strip() or f"{package} is not installed",
            )

        launched = self._run(
            [
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ],
            timeout=12,
        )
        ok = launched.returncode == 0 and "No activities found" not in launched.stdout
        return self._result(
            ok,
            "ok" if ok else "launch_failed",
            command,
            started,
            message=f"directly launched {app_name}",
            data={
                "app": app_name,
                "package": package,
                "stdout": _tail(launched.stdout),
                "stderr": _tail(launched.stderr),
            },
            error="" if ok else (launched.stderr or launched.stdout).strip(),
        )

    def _keyevent(self, command: DirectPhoneCommand, started: float, keycode: str) -> DirectPhoneResult:
        result = self._run(["shell", "input", "keyevent", keycode], timeout=5)
        ok = result.returncode == 0
        return self._result(
            ok,
            "ok" if ok else "keyevent_failed",
            command,
            started,
            data={"keycode": keycode, "stdout": _tail(result.stdout), "stderr": _tail(result.stderr)},
            error="" if ok else (result.stderr or result.stdout).strip(),
        )

    def _tap(self, command: DirectPhoneCommand, started: float) -> DirectPhoneResult:
        if command.x is None or command.y is None:
            return self._result(False, "invalid_tap", command, started, error="tap requires x and y")
        result = self._run(["shell", "input", "tap", str(command.x), str(command.y)], timeout=5)
        ok = result.returncode == 0
        return self._result(
            ok,
            "ok" if ok else "tap_failed",
            command,
            started,
            data={"x": command.x, "y": command.y, "stdout": _tail(result.stdout), "stderr": _tail(result.stderr)},
            error="" if ok else (result.stderr or result.stdout).strip(),
        )

    def _tap_text(self, command: DirectPhoneCommand, started: float) -> DirectPhoneResult:
        nodes = self._dump_clickable_nodes()
        target = _find_node_by_text(nodes, command.value)
        if target is None:
            return self._result(
                False,
                "text_not_found",
                command,
                started,
                data={"text": command.value, "candidates": [node["label"] for node in nodes[:20]]},
                error=f"text not found in UIAutomator tree: {command.value}",
            )
        x1, y1, x2, y2 = target["bounds"]
        tap_command = DirectPhoneCommand(kind="tap", x=(x1 + x2) // 2, y=(y1 + y2) // 2, value=command.value)
        result = self._tap(tap_command, started)
        result.action = "tap_text"
        result.data.update({"text": command.value, "matched": target})
        return result

    def _type_text(self, command: DirectPhoneCommand, started: float) -> DirectPhoneResult:
        if self.config.auto_install_adb_keyboard:
            self._ensure_adb_keyboard()
            original_ime = self._current_ime()
            self._run(["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"], timeout=8)
            self._run(["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"], timeout=5)
            encoded = base64.b64encode(command.value.encode("utf-8")).decode("ascii")
            result = self._run(["shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded], timeout=8)
            if original_ime and "com.android.adbkeyboard/.AdbIME" not in original_ime:
                self._run(["shell", "ime", "set", original_ime], timeout=8)
        else:
            result = self._run(["shell", "input", "text", _escape_input_text(command.value)], timeout=8)
        ok = result.returncode == 0
        return self._result(
            ok,
            "ok" if ok else "type_failed",
            command,
            started,
            data={"text_length": len(command.value), "stdout": _tail(result.stdout), "stderr": _tail(result.stderr)},
            error="" if ok else (result.stderr or result.stdout).strip(),
        )

    def _swipe(self, command: DirectPhoneCommand, started: float) -> DirectPhoneResult:
        coords = command.metadata.get("coords") or ()
        if len(coords) != 5:
            return self._result(False, "invalid_swipe", command, started, error="swipe requires coordinates")
        result = self._run(["shell", "input", "swipe", *(str(v) for v in coords)], timeout=8)
        ok = result.returncode == 0
        return self._result(
            ok,
            "ok" if ok else "swipe_failed",
            command,
            started,
            data={"coords": coords, "stdout": _tail(result.stdout), "stderr": _tail(result.stderr)},
            error="" if ok else (result.stderr or result.stdout).strip(),
        )

    def _dump_clickable_nodes(self) -> list[dict[str, Any]]:
        dump = self._run(["shell", "uiautomator", "dump", "/sdcard/lampgo-window.xml"], timeout=8)
        if dump.returncode != 0:
            return []
        cat = self._run(["exec-out", "cat", "/sdcard/lampgo-window.xml"], timeout=8)
        if cat.returncode != 0 or not cat.stdout:
            return []
        try:
            root = ET.fromstring(cat.stdout)
        except ET.ParseError:
            return []

        nodes: list[dict[str, Any]] = []
        for node in root.iter("node"):
            text = (node.attrib.get("text") or "").strip()
            desc = (node.attrib.get("content-desc") or "").strip()
            label = text or desc
            bounds = _parse_bounds(node.attrib.get("bounds") or "")
            if not label or bounds is None:
                continue
            nodes.append(
                {
                    "label": label,
                    "text": text,
                    "content_desc": desc,
                    "resource_id": node.attrib.get("resource-id") or "",
                    "bounds": bounds,
                    "clickable": node.attrib.get("clickable") == "true",
                }
            )
        return nodes

    def _ensure_adb_keyboard(self) -> None:
        ime = self._run(["shell", "ime", "list", "-s"], timeout=8)
        if "com.android.adbkeyboard/.AdbIME" in (ime.stdout + ime.stderr):
            return
        apk = files("lampgo.vendor.open_autoglm").joinpath("tools/ADBKeyboard.apk")
        self._run(["install", "-r", str(apk)], timeout=30)
        self._run(["shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME"], timeout=8)

    def _current_ime(self) -> str:
        result = self._run(["shell", "settings", "get", "secure", "default_input_method"], timeout=8)
        return (result.stdout + result.stderr).strip()

    def _run(self, args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._adb_prefix() + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    def _adb_prefix(self) -> list[str]:
        prefix = [self.adb]
        if self.device_id:
            prefix.extend(["-s", self.device_id])
        return prefix

    def _result(
        self,
        ok: bool,
        status: str,
        command: DirectPhoneCommand,
        started: float,
        *,
        message: str = "",
        data: dict[str, Any] | None = None,
        error: str = "",
    ) -> DirectPhoneResult:
        return DirectPhoneResult(
            ok=ok,
            status=status,
            action=command.kind,
            duration_s=round(time.monotonic() - started, 3),
            message=message,
            data=data or {"value": command.value},
            error=error,
        )


def _normalize_task_text(task: str) -> str:
    text = re.sub(r"\s+", " ", (task or "").strip())
    text = re.sub(r"\n+", "。", text)
    return text.strip()


def _parse_launch(text: str) -> DirectPhoneCommand | None:
    app = _match_first(
        text,
        [
            r"(?:app|应用)\s*参数\s*(?:使用|用|为|=|:|：)?\s*(?P<value>[A-Za-z0-9_. \-\u4e00-\u9fff]{2,40})",
            r"(?:Launch|launch)\s*(?:操作)?[^。；;,\n]{0,40}?(?:app|应用)?\s*(?:参数)?\s*(?:使用|用|为|=|:|：)\s*(?P<value>[A-Za-z0-9_. \-\u4e00-\u9fff]{2,40})",
            r"(?:打开|启动|进入|运行)\s*(?:手机|安卓|Android)?\s*(?P<value>[A-Za-z0-9_. \-\u4e00-\u9fff]{2,40}?)(?:应用|软件|app)?(?:[。；;,，]|$)",
            r"(?:open|launch|start)\s+(?:the\s+)?(?P<value>[A-Za-z0-9_. \-\u4e00-\u9fff]{2,40}?)(?:\s+app)?(?:[。；;,，]|$)",
        ],
    )
    if not app:
        return None
    if _FOLLOWUP_VERB_RE.search(app):
        return None
    if _has_complex_followup(text, app):
        return None
    app = _clean_app_name(app)
    if not app:
        return None
    return DirectPhoneCommand(kind="launch", value=app)


def _parse_key_or_motion(text: str) -> DirectPhoneCommand | None:
    if re.search(r"(返回上一页|按返回|返回键|back\b)", text, re.IGNORECASE):
        return DirectPhoneCommand(kind="back")
    if re.search(r"(回到桌面|返回桌面|返回手机桌面|回主屏|回首页|按Home|home\b|主屏幕)", text, re.IGNORECASE):
        return DirectPhoneCommand(kind="home")
    if re.search(r"(按回车|确认键|enter\b)", text, re.IGNORECASE):
        return DirectPhoneCommand(kind="enter")

    directions = {
        "上滑": (540, 1600, 540, 500, 500),
        "向上滑": (540, 1600, 540, 500, 500),
        "下滑": (540, 500, 540, 1600, 500),
        "向下滑": (540, 500, 540, 1600, 500),
        "左滑": (900, 1000, 150, 1000, 500),
        "向左滑": (900, 1000, 150, 1000, 500),
        "右滑": (150, 1000, 900, 1000, 500),
        "向右滑": (150, 1000, 900, 1000, 500),
    }
    for token, coords in directions.items():
        if token in text:
            return DirectPhoneCommand(kind="swipe", value=token, metadata={"coords": coords})
    return None


def _parse_tap_coordinates(text: str) -> DirectPhoneCommand | None:
    match = re.search(r"(?:点击|点按|tap|click)[^\d]{0,8}(?P<x>\d{2,4})\s*[,， ]\s*(?P<y>\d{2,4})", text, re.IGNORECASE)
    if not match:
        return None
    return DirectPhoneCommand(kind="tap", x=int(match.group("x")), y=int(match.group("y")))


def _parse_tap_text(text: str) -> DirectPhoneCommand | None:
    value = _match_first(
        text,
        [
            r"(?:点击|点按|选择|tap|click)\s*[“\"']?(?P<value>[^”\"'。；;,，]{1,32})[”\"']?(?:[。；;,，]|$)",
        ],
    )
    if not value:
        return None
    value = value.strip()
    if _SENSITIVE_DIRECT_RE.search(value):
        return None
    return DirectPhoneCommand(kind="tap_text", value=value)


def _parse_type_text(text: str) -> DirectPhoneCommand | None:
    value = _match_first(
        text,
        [
            r"(?:输入|填写|type|input)\s*[“\"'](?P<value>[^”\"']{1,120})[”\"']",
            r"(?:输入|填写)\s*(?P<value>[^。；;,，]{1,80})(?:[。；;,，]|$)",
            r"(?:type|input)\s+(?P<value>[^。；;,，]{1,80})(?:[。；;,，]|$)",
        ],
    )
    if not value:
        return None
    return DirectPhoneCommand(kind="type", value=value.strip())


def _match_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return (match.group("value") or "").strip()
    return ""


def _has_complex_followup(text: str, app: str) -> bool:
    index = text.find(app)
    if index < 0:
        return False
    remainder = text[index + len(app):]
    remainder = re.sub(r"(应用|软件|app|。|，|,|;|；|如果已经打开.*?finish|优先使用\s*Launch.*)$", "", remainder, flags=re.IGNORECASE)
    return bool(_FOLLOWUP_VERB_RE.search(remainder))


def _clean_app_name(value: str) -> str:
    value = value.strip(" “”。,，;；:：\"'")
    value = re.sub(r"(应用|软件|app)$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"^(手机|安卓|Android)\s*", "", value, flags=re.IGNORECASE).strip()
    return _APP_ALIASES.get(value, value)


def _resolve_package(app_name: str) -> str:
    cleaned = _clean_app_name(app_name)
    if _PACKAGE_RE.fullmatch(cleaned):
        return cleaned
    alias = _APP_ALIASES.get(cleaned, cleaned)
    if _PACKAGE_RE.fullmatch(alias):
        return alias
    return APP_PACKAGES.get(alias) or APP_PACKAGES.get(cleaned) or ""


def _find_node_by_text(nodes: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    normalized_query = _normalize_label(query)
    if not normalized_query:
        return None
    exact: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []
    for node in nodes:
        label = _normalize_label(str(node.get("label") or ""))
        if not label:
            continue
        if label == normalized_query:
            exact.append(node)
        elif normalized_query in label:
            contains.append(node)
    candidates = exact or contains
    if not candidates:
        return None
    candidates.sort(key=lambda node: (not bool(node.get("clickable")), _bounds_area(node["bounds"])))
    return candidates[0]


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", "", value.strip()).lower()


def _parse_bounds(value: str) -> tuple[int, int, int, int] | None:
    match = _BOUNDS_RE.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _bounds_area(bounds: tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = bounds
    return max(0, x2 - x1) * max(0, y2 - y1)


def _escape_input_text(value: str) -> str:
    return value.replace("%", "\\%").replace(" ", "%s")


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]
