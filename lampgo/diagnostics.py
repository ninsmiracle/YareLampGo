"""Bounded, redacted runtime logs and local support bundles."""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
import traceback
import zipfile
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from lampgo.personastore import lampgo_home

_logger = None
_private = re.compile(r"secret|password|token|authorization|api_key|auth_proof|auth_nonce|credential", re.I)
_content = {"messages", "prompt", "content", "transcript", "audio", "image", "input", "content_preview"}


def redact(value):
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if _private.search(str(k)) or str(k) in _content else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(Bearer\s+)[^\s'\"]+", r"\1[redacted]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
        value = re.sub(r"(?i)((?:api_key|token|secret|password|auth_proof)=)[^\s&]+", r"\1[redacted]", value)
        return value[:16000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact(str(value))


def runtime_log_path() -> Path:
    import os

    return lampgo_home() / "logs" / f"runtime-{os.getpid()}.log"


def install_exception_logging() -> None:
    import threading

    previous = sys.excepthook

    def report(kind, value, tb):
        persist_log(None, "error", {"event": "process.unhandled_exception", "exc_info": (kind, value, tb)})
        previous(kind, value, tb)

    sys.excepthook = report
    previous_thread = threading.excepthook

    def report_thread(args):
        persist_log(
            None,
            "error",
            {"event": "thread.unhandled_exception", "exc_info": (args.exc_type, args.exc_value, args.exc_traceback)},
        )
        previous_thread(args)

    threading.excepthook = report_thread


def persist_log(_logger_arg, method: str, event: dict):
    global _logger
    try:
        if _logger is None:
            path = runtime_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _logger = logging.getLogger("lampgo.support")
            _logger.propagate = False
            _logger.setLevel(logging.INFO)
            handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(handler)
        record = dict(event)
        exception = record.pop("exc_info", None)
        if exception:
            if exception is True:
                exception = sys.exc_info()
            if isinstance(exception, tuple):
                record["traceback"] = "".join(traceback.format_exception(*exception))
        record.update(timestamp=datetime.now(UTC).isoformat(), level=method)
        _logger.info(json.dumps(redact(record), ensure_ascii=False))
    except Exception:
        pass  # Disk-full must not interrupt a servo safety operation.
    return event


def support_bundle(server) -> Path:
    directory = lampgo_home() / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("lampgo-diagnostics-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f") + ".zip")
    transport = getattr(server.hal, "transport_status", lambda: {})()
    status = {
        "created_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "transport": transport,
        "maintenance": server.maintenance.status(),
        "motor_transport": server.config.device.motor_transport,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("status.json", json.dumps(redact(status), ensure_ascii=False, indent=2))
        archive.writestr(
            "README.txt", "把整个 ZIP 文件发给技术人员。包含脱敏运行日志和设备状态；不包含凭据文件、录音或照片。\n"
        )
        files = sorted((lampgo_home() / "logs").glob("runtime-*.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for log in files[:4]:
            # Redact again in case a log was created by an earlier backend.
            lines = []
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    lines.append(json.dumps(redact(json.loads(line)), ensure_ascii=False))
                except ValueError:
                    lines.append(redact(line))
            archive.writestr("logs/" + log.name, "\n".join(lines))
    return path
