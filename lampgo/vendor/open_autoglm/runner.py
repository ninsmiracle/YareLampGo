"""Lampgo-internal runner for the vendored Open-AutoGLM phone agent."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from lampgo.vendor.open_autoglm.phone_agent import PhoneAgent
from lampgo.vendor.open_autoglm.phone_agent.agent import AgentConfig
from lampgo.vendor.open_autoglm.phone_agent.agent_ios import IOSAgentConfig, IOSPhoneAgent
from lampgo.vendor.open_autoglm.phone_agent.device_factory import DeviceType, set_device_type
from lampgo.vendor.open_autoglm.phone_agent.model import ModelConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Lampgo's built-in Open-AutoGLM phone agent.")
    parser.add_argument("task", help="Natural-language phone task.")
    parser.add_argument(
        "--device-type",
        choices=["adb", "hdc", "ios"],
        default=os.getenv("PHONE_AGENT_DEVICE_TYPE", "adb"),
    )
    parser.add_argument("--device-id", default=os.getenv("PHONE_AGENT_DEVICE_ID", ""))
    parser.add_argument("--base-url", default=os.getenv("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--model", default=os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b"))
    parser.add_argument("--api-key", default=os.getenv("PHONE_AGENT_API_KEY", "EMPTY"))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("PHONE_AGENT_MAX_STEPS", "100")))
    parser.add_argument("--lang", choices=["cn", "en"], default=os.getenv("PHONE_AGENT_LANG", "cn"))
    parser.add_argument("--wda-url", default=os.getenv("PHONE_AGENT_WDA_URL", "http://localhost:8100"))
    parser.add_argument("--allow-sensitive", action="store_true")
    parser.add_argument("--auto-install-adb-keyboard", action="store_true")
    args = parser.parse_args(argv)

    model_config = ModelConfig(
        base_url=args.base_url,
        api_key=args.api_key or "EMPTY",
        model_name=args.model,
        lang=args.lang,
    )

    if args.device_type == "ios":
        agent = IOSPhoneAgent(
            model_config=model_config,
            agent_config=IOSAgentConfig(
                max_steps=args.max_steps,
                wda_url=args.wda_url,
                device_id=args.device_id or None,
                lang=args.lang,
                verbose=True,
            ),
            confirmation_callback=_confirmation_callback(args.allow_sensitive),
            takeover_callback=_takeover_callback,
        )
    else:
        set_device_type(DeviceType(args.device_type))
        if args.device_type == "adb" and args.auto_install_adb_keyboard:
            _ensure_adb_keyboard(args.device_id or None)
        agent = PhoneAgent(
            model_config=model_config,
            agent_config=AgentConfig(
                max_steps=args.max_steps,
                device_id=args.device_id or None,
                lang=args.lang,
                verbose=True,
            ),
            confirmation_callback=_confirmation_callback(args.allow_sensitive),
            takeover_callback=_takeover_callback,
        )

    message = agent.run(args.task)
    print(message)
    return 0


def _confirmation_callback(allow_sensitive: bool):
    def _confirm(message: str) -> bool:
        if allow_sensitive:
            print(f"Sensitive operation allowed by caller: {message}")
            return True
        print(f"Sensitive operation blocked by Lampgo policy: {message}")
        return False

    return _confirm


def _takeover_callback(message: str) -> None:
    print(f"Manual takeover requested: {message}")


def _ensure_adb_keyboard(device_id: str | None) -> None:
    prefix = ["adb"]
    if device_id:
        prefix.extend(["-s", device_id])
    try:
        ime = subprocess.run(
            prefix + ["shell", "ime", "list", "-s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if "com.android.adbkeyboard/.AdbIME" not in (ime.stdout + ime.stderr):
            apk = Path(files("lampgo.vendor.open_autoglm").joinpath("tools/ADBKeyboard.apk"))
            subprocess.run(prefix + ["install", "-r", str(apk)], capture_output=True, text=True, timeout=30)
            subprocess.run(
                prefix + ["shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME"],
                capture_output=True,
                text=True,
                timeout=8,
            )
    except Exception as exc:
        print(f"ADB Keyboard auto-install skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
