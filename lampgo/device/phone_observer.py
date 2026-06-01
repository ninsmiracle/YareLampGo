"""ADB-based phone observation and lightweight result verification."""

from __future__ import annotations

import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from lampgo.device.phone_paths import find_adb


@dataclass
class PhoneObservation:
    ok: bool
    status: str
    screenshot_path: str = ""
    xml_path: str = ""
    foreground_package: str = ""
    screen_text: str = ""
    text_nodes: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self, *, text_limit: int = 1200, node_limit: int = 40) -> dict[str, Any]:
        text = self.screen_text.strip()
        if len(text) > text_limit:
            text = f"{text[:text_limit]}..."
        return {
            "ok": self.ok,
            "status": self.status,
            "screenshot_path": self.screenshot_path,
            "xml_path": self.xml_path,
            "foreground_package": self.foreground_package,
            "screen_text": text,
            "text_nodes": self.text_nodes[:node_limit],
            "metrics": self.metrics,
            "error": self.error,
        }


def capture_phone_observation(
    *,
    task: str = "",
    device_id: str = "",
    device_type: str = "adb",
    adb_path: str = "",
    artifact_dir: str | Path = ".lampgo/phone-artifacts",
    timeout_s: float = 12.0,
) -> PhoneObservation:
    """Capture final screenshot and UIAutomator state from the connected phone."""
    del task  # Reserved for richer observers; verification consumes the task separately.
    if device_type.strip().lower() != "adb":
        return PhoneObservation(
            ok=True,
            status="unsupported",
            error=f"result observation is not implemented for device_type={device_type}",
        )
    adb = find_adb(adb_path)
    if not adb:
        return PhoneObservation(ok=False, status="adb_missing", error="adb not found")

    prefix = [adb]
    if device_id:
        prefix.extend(["-s", device_id])

    root = Path(artifact_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
    screenshot_path = root / f"{run_id}.png"
    xml_path = root / f"{run_id}.xml"

    try:
        screenshot = subprocess.run(
            prefix + ["exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=timeout_s,
        )
        if screenshot.returncode != 0 or not screenshot.stdout:
            return PhoneObservation(
                ok=False,
                status="screenshot_failed",
                screenshot_path=str(screenshot_path),
                error=(screenshot.stderr or b"").decode("utf-8", errors="replace").strip(),
            )
        screenshot_path.write_bytes(screenshot.stdout)

        dump = subprocess.run(
            prefix + ["shell", "uiautomator", "dump", "/sdcard/lampgo-window.xml"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        xml_bytes = b""
        if dump.returncode == 0:
            cat = subprocess.run(
                prefix + ["exec-out", "cat", "/sdcard/lampgo-window.xml"],
                capture_output=True,
                timeout=timeout_s,
            )
            xml_bytes = cat.stdout or b""
            if xml_bytes:
                xml_path.write_bytes(xml_bytes)

        focus = subprocess.run(
            prefix + ["shell", "dumpsys", "window"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except Exception as exc:
        return PhoneObservation(
            ok=False,
            status="capture_error",
            screenshot_path=str(screenshot_path),
            xml_path=str(xml_path),
            error=str(exc),
        )

    text_nodes = _extract_text_nodes(xml_bytes)
    screen_text = " ".join(text_nodes)
    metrics = _image_metrics(screenshot.stdout)
    metrics["text_node_count"] = len(text_nodes)
    metrics["is_probably_blank"] = _is_probably_blank(metrics, text_nodes)

    return PhoneObservation(
        ok=True,
        status="ok",
        screenshot_path=str(screenshot_path),
        xml_path=str(xml_path) if xml_bytes else "",
        foreground_package=_extract_foreground_package(focus.stdout),
        screen_text=screen_text,
        text_nodes=text_nodes,
        metrics=metrics,
    )


def verify_phone_task_result(task: str, observation: PhoneObservation) -> dict[str, Any]:
    """Return a conservative verification result from screenshot/UI state.

    This is intentionally lightweight and generic: it catches obvious failures
    such as blank pages or missing search/input terms, then surfaces the final
    screenshot for human or future VLM review.
    """
    reasons: list[str] = []
    if not observation.ok:
        return {
            "status": "failed",
            "ok": False,
            "confidence": "high",
            "reasons": [observation.error or observation.status],
            "expected_texts": [],
        }
    if observation.status == "unsupported":
        return {
            "status": "needs_review",
            "ok": True,
            "confidence": "low",
            "reasons": [observation.error or observation.status],
            "expected_texts": extract_expected_texts(task),
            "missing_texts": [],
        }

    if observation.metrics.get("is_probably_blank"):
        reasons.append("final_screen_looks_blank_or_unreadable")

    expected = extract_expected_texts(task)
    normalized_screen = _normalize_text(observation.screen_text)
    missing = [term for term in expected if _normalize_text(term) not in normalized_screen]
    if expected and missing:
        reasons.append("expected_text_not_found_in_accessibility_tree")

    if reasons and "final_screen_looks_blank_or_unreadable" in reasons:
        status = "failed"
        ok = False
        confidence = "medium"
    elif expected and not missing:
        status = "verified"
        ok = True
        confidence = "medium"
    else:
        status = "needs_review"
        ok = True
        confidence = "low"

    return {
        "status": status,
        "ok": ok,
        "confidence": confidence,
        "reasons": reasons,
        "expected_texts": expected,
        "missing_texts": missing,
    }


def extract_expected_texts(task: str) -> list[str]:
    """Extract user-visible target text from common Chinese/English task phrasing."""
    task = (task or "").strip()
    candidates: list[str] = []

    for match in re.finditer(r"[“\"'](?P<value>[^”\"']{2,40})[”\"']", task):
        candidates.append(match.group("value"))

    patterns = [
        r"(?:搜索(?!框)|搜一下|搜(?!索框)|查找|查询)\s*(?P<value>[^，,。；;\n]{2,40})",
        r"(?:输入|填写)\s*[“\"']?(?P<value>[^”\"'，,。；;\n]{2,40})",
        r"(?:search(?: for)?|type|input)\s+(?P<value>[^,.;\n]{2,40})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, task, flags=re.IGNORECASE):
            value = _clean_expected_text(match.group("value"))
            if value:
                candidates.append(value)

    out: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        value = _clean_expected_text(value)
        key = _normalize_text(value)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out[:5]


def _clean_expected_text(value: str) -> str:
    value = re.sub(r"(并进行搜索|进行搜索|并搜索|搜索|然后|打开|进入|结果.*)$", "", value.strip())
    return value.strip(" “”。,，;；:：\"'")


def _extract_text_nodes(xml_bytes: bytes) -> list[str]:
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return []

    values: list[str] = []
    seen: set[str] = set()
    for node in root.iter("node"):
        for attr in ("text", "content-desc"):
            value = (node.attrib.get(attr) or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
    return values


def _image_metrics(image_bytes: bytes) -> dict[str, Any]:
    metrics: dict[str, Any] = {"byte_size": len(image_bytes or b"")}
    if not image_bytes:
        return metrics
    try:
        from PIL import Image, ImageStat

        with Image.open(BytesIO(image_bytes)) as image:
            metrics["width"] = image.width
            metrics["height"] = image.height
            sample = image.convert("RGB").resize((64, 64))
            pixels = list(sample.getdata())
            buckets = Counter((r // 16, g // 16, b // 16) for r, g, b in pixels)
            metrics["dominant_color_ratio"] = round(max(buckets.values()) / max(1, len(pixels)), 4)
            gray_stat = ImageStat.Stat(sample.convert("L"))
            metrics["gray_stddev"] = round(float(gray_stat.stddev[0]), 3)
    except Exception as exc:
        metrics["image_metric_error"] = str(exc)
    return metrics


def _is_probably_blank(metrics: dict[str, Any], text_nodes: list[str]) -> bool:
    if metrics.get("byte_size", 0) < 1024:
        return True
    dominant = float(metrics.get("dominant_color_ratio") or 0.0)
    stddev = float(metrics.get("gray_stddev") or 999.0)
    text_count = len([text for text in text_nodes if text.strip()])
    return text_count <= 2 and (dominant >= 0.985 or stddev <= 6.0)


def _extract_foreground_package(text: str) -> str:
    patterns = [
        r"mCurrentFocus=.*?\s([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/",
        r"mFocusedApp=.*?\s([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/",
        r"topResumedActivity=.*?\s([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            return match.group(1)
    return ""


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


__all__ = [
    "PhoneObservation",
    "capture_phone_observation",
    "extract_expected_texts",
    "verify_phone_task_result",
]
