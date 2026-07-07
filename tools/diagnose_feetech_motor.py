#!/usr/bin/env python3
"""Diagnose one Feetech motor connected directly to the driver board.

This tool is intentionally outside the normal lampgo runtime because it is for
bench diagnosis: one motor on the bus, no arm-level calibration assumptions.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

READ_REGISTERS = [
    "Model_Number",
    "ID",
    "Baud_Rate",
    "Torque_Enable",
    "Lock",
    "Operating_Mode",
    "Min_Position_Limit",
    "Max_Position_Limit",
    "Homing_Offset",
    "Goal_Position",
    "Present_Position",
    "Present_Velocity",
    "Present_Load",
    "Present_Current",
    "Present_Temperature",
    "Present_Voltage",
    "Status",
    "Acceleration",
    "P_Coefficient",
    "I_Coefficient",
    "D_Coefficient",
    "Torque_Limit",
    "Protection_Current",
    "Overload_Torque",
    "Protection_Time",
]


@dataclass
class Diagnostic:
    port: str
    motor_id: int
    model: str
    ok: bool = True
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def fail(self, text: str) -> None:
        self.ok = False
        self.findings.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    def note(self, text: str) -> None:
        self.findings.append(text)


def _load_lerobot():
    try:
        from lerobot.motors import Motor, MotorNormMode
        from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
    except ImportError as exc:
        raise RuntimeError("lerobot[feetech] is not installed. Run through `uv run ...`.") from exc
    return Motor, MotorNormMode, FeetechMotorsBus, OperatingMode


def _detect_port() -> str | None:
    try:
        from lampgo.autodetect import detect_ports
    except Exception:
        return None

    detected = detect_ports()
    return str(detected.get("motor_port") or "").strip() or None


def _tx_result(bus: Any, comm: int) -> str:
    try:
        return str(bus.packet_handler.getTxRxResult(comm))
    except Exception:
        return str(comm)


def _rx_error(bus: Any, error: int) -> str:
    try:
        return str(bus.packet_handler.getRxPacketError(error))
    except Exception:
        return str(error)


def _connect_bus(port: str, motor_id: int, model: str):
    Motor, MotorNormMode, FeetechMotorsBus, _OperatingMode = _load_lerobot()
    name = f"motor_{motor_id}"
    motors = {name: Motor(motor_id, model, MotorNormMode.DEGREES)}
    bus = FeetechMotorsBus(port=port, motors=motors)
    bus.connect(handshake=False)
    return bus, name


def _close_bus(bus: Any) -> None:
    try:
        bus.disable_torque(num_retry=3)
    except Exception:
        pass
    try:
        bus.port_handler.closePort()
    except Exception:
        pass


def _read(bus: Any, motor_name: str, register: str) -> Any:
    try:
        return bus.read(register, motor_name, normalize=False)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _snapshot(bus: Any, motor_name: str, registers: Iterable[str] = READ_REGISTERS) -> dict[str, Any]:
    return {register: _read(bus, motor_name, register) for register in registers}


def _write_checked(
    bus: Any,
    motor_name: str,
    register: str,
    value: int,
    *,
    normalize: bool = False,
    verify: bool = True,
) -> None:
    bus.write(register, motor_name, value, normalize=normalize, num_retry=3)
    if not verify:
        return
    actual = bus.read(register, motor_name, normalize=normalize)
    if int(actual) != int(value):
        raise RuntimeError(f"{register} verify failed: expected {value}, got {actual}")


def _ping(bus: Any, motor_id: int) -> dict[str, Any]:
    try:
        model_number, comm, error = bus.packet_handler.ping(bus.port_handler, motor_id)
    except Exception as exc:
        return {
            "ok": False,
            "comm_ok": False,
            "status_ok": False,
            "model_number": None,
            "comm": None,
            "comm_text": f"{type(exc).__name__}: {exc}",
            "status_error": None,
            "status_text": "",
        }
    ok = bool(bus._is_comm_success(comm))
    status_ok = not bool(bus._is_error(error)) if ok else False
    return {
        "ok": ok and status_ok,
        "comm_ok": ok,
        "status_ok": status_ok,
        "model_number": int(model_number) if ok else None,
        "comm": int(comm),
        "comm_text": _tx_result(bus, comm),
        "status_error": int(error),
        "status_text": _rx_error(bus, error) if ok and not status_ok else "",
    }


def _scan_ids(bus: Any, start: int, end: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for motor_id in range(start, end + 1):
        result = _ping(bus, motor_id)
        if result["comm_ok"]:
            result["id"] = motor_id
            found.append(result)
    return found


def _set_motor_id(bus: Any, motor_name: str, old_id: int, new_id: int, *, assume_yes: bool) -> dict[str, Any]:
    if not assume_yes:
        raise RuntimeError("Refusing to change motor ID without --yes. Connect only one motor before using --set-id.")
    if not 0 <= new_id <= 253:
        raise ValueError("--set-id must be in range 0..253")

    local_scan = _scan_ids(bus, 0, 20)
    found_ids = [int(item["id"]) for item in local_scan if item.get("comm_ok")]
    if found_ids != [old_id]:
        raise RuntimeError(
            "Refusing to change ID because scan did not find exactly the requested single motor. "
            f"found={found_ids}, requested={old_id}. Disconnect all other motors and retry."
        )

    _write_checked(bus, motor_name, "Torque_Enable", 0, normalize=True, verify=False)
    _write_checked(bus, motor_name, "Lock", 0, normalize=True, verify=False)
    _write_checked(bus, motor_name, "ID", new_id, normalize=False, verify=False)
    time.sleep(0.2)
    return {
        "old_id": old_id,
        "new_id": new_id,
        "pre_scan_ids": found_ids,
    }


def _release_for_manual_motion(bus: Any, motor_name: str) -> None:
    _write_checked(bus, motor_name, "Torque_Enable", 0, normalize=True, verify=False)
    _write_checked(bus, motor_name, "Lock", 0, normalize=True, verify=False)
    bus.disable_torque([motor_name], num_retry=3)
    torque = bus.read("Torque_Enable", motor_name, normalize=False)
    lock = bus.read("Lock", motor_name, normalize=False)
    if int(torque) != 0 or int(lock) != 0:
        raise RuntimeError(f"torque release failed: Torque_Enable={torque}, Lock={lock}")


def _open_full_range(bus: Any, motor_name: str) -> dict[str, int]:
    motor = bus.motors[motor_name]
    max_position = int(bus.model_resolution_table[motor.model]) - 1
    _write_checked(bus, motor_name, "Homing_Offset", 0, normalize=False)
    _write_checked(bus, motor_name, "Min_Position_Limit", 0, normalize=False)
    _write_checked(bus, motor_name, "Max_Position_Limit", max_position, normalize=False)
    return {"min": 0, "max": max_position, "homing_offset": 0}


def _sample(bus: Any, motor_name: str, seconds: float, interval_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    samples: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        item = {
            "t": round(time.monotonic(), 4),
            "position": _read(bus, motor_name, "Present_Position"),
            "current": _read(bus, motor_name, "Present_Current"),
            "load": _read(bus, motor_name, "Present_Load"),
            "status": _read(bus, motor_name, "Status"),
        }
        samples.append(item)
        time.sleep(interval_s)

    positions = [int(s["position"]) for s in samples if isinstance(s.get("position"), int)]
    currents = [int(s["current"]) for s in samples if isinstance(s.get("current"), int)]
    loads = [int(s["load"]) for s in samples if isinstance(s.get("load"), int)]
    return {
        "samples": samples,
        "count": len(samples),
        "position_min": min(positions) if positions else None,
        "position_max": max(positions) if positions else None,
        "position_span": (max(positions) - min(positions)) if positions else None,
        "current_max": max(currents) if currents else None,
        "load_abs_max": max((abs(v) for v in loads), default=None),
    }


def _watch(bus: Any, motor_name: str, interval_s: float) -> dict[str, Any]:
    print("Live watch. Move the motor by hand; press Ctrl+C to stop.", flush=True)
    samples: list[dict[str, Any]] = []
    pos_min: int | None = None
    pos_max: int | None = None
    try:
        while True:
            position = _read(bus, motor_name, "Present_Position")
            current = _read(bus, motor_name, "Present_Current")
            load = _read(bus, motor_name, "Present_Load")
            status = _read(bus, motor_name, "Status")
            if isinstance(position, int):
                pos_min = position if pos_min is None else min(pos_min, position)
                pos_max = position if pos_max is None else max(pos_max, position)
            span = (pos_max - pos_min) if pos_min is not None and pos_max is not None else None
            print(
                f"pos={position} span={span} min={pos_min} max={pos_max} "
                f"current={current} load={load} status={status}",
                flush=True,
            )
            samples.append(
                {
                    "position": position,
                    "current": current,
                    "load": load,
                    "status": status,
                }
            )
            time.sleep(interval_s)
    except KeyboardInterrupt:
        print("Stopped live watch.", flush=True)

    return {
        "samples": samples,
        "count": len(samples),
        "position_min": pos_min,
        "position_max": pos_max,
        "position_span": (pos_max - pos_min) if pos_min is not None and pos_max is not None else None,
    }


def _bounded_target(start: int, delta: int, lo: int, hi: int, direction: int) -> int:
    target = start + direction * abs(delta)
    if lo <= target <= hi:
        return target
    return start - direction * abs(delta)


def _active_test(bus: Any, motor_name: str, delta: int, move_time_ms: int) -> dict[str, Any]:
    _load_lerobot()
    _release_for_manual_motion(bus, motor_name)
    _write_checked(bus, motor_name, "Operating_Mode", 0, normalize=True)
    try:
        _write_checked(bus, motor_name, "Acceleration", 10, normalize=True, verify=False)
    except Exception:
        pass

    start = int(bus.read("Present_Position", motor_name, normalize=False))
    lo = int(bus.read("Min_Position_Limit", motor_name, normalize=False))
    hi = int(bus.read("Max_Position_Limit", motor_name, normalize=False))
    target_a = _bounded_target(start, delta, lo, hi, 1)
    target_b = _bounded_target(start, delta, lo, hi, -1)

    _write_checked(bus, motor_name, "Goal_Position", start, normalize=False)
    _write_checked(bus, motor_name, "Lock", 1, normalize=True, verify=False)
    _write_checked(bus, motor_name, "Torque_Enable", 1, normalize=True, verify=True)
    time.sleep(0.2)

    segments: list[dict[str, Any]] = []
    for target in [target_a, start, target_b, start]:
        try:
            _write_checked(bus, motor_name, "Goal_Time", move_time_ms, normalize=False, verify=False)
        except Exception:
            pass
        _write_checked(bus, motor_name, "Goal_Position", int(target), normalize=False)
        samples = _sample(bus, motor_name, max(move_time_ms / 1000.0 + 0.25, 0.35), 0.05)
        segments.append({"target": int(target), **samples})

    _release_for_manual_motion(bus, motor_name)

    all_positions = [
        int(s["position"])
        for segment in segments
        for s in segment["samples"]
        if isinstance(s.get("position"), int)
    ]
    span = (max(all_positions) - min(all_positions)) if all_positions else 0
    return {
        "start_position": start,
        "delta_command": abs(delta),
        "targets": [int(target_a), start, int(target_b), start],
        "observed_span": span,
        "segments": segments,
    }


def _classify(report: Diagnostic, manual_seconds: float, active: bool) -> None:
    ping = report.data.get("ping") or {}
    before = report.data.get("snapshot_before") or {}
    manual = report.data.get("manual_sample") or {}
    active_result = report.data.get("active_test") or {}

    if not ping.get("comm_ok"):
        report.fail("No response from the requested motor ID. Check ID, power, cable, and driver board first.")
        return
    if not ping.get("status_ok"):
        report.fail(f"Motor responded but reported status error: {ping.get('status_text') or ping.get('status_error')}")
    else:
        report.note("Ping and status packet are OK.")

    status = before.get("Status")
    if isinstance(status, int) and status != 0:
        report.fail(f"Status register is non-zero before testing: {status}.")

    voltage = before.get("Present_Voltage")
    if isinstance(voltage, int) and voltage < 95:
        report.warn(f"Voltage looks low ({voltage / 10:.1f} V if Feetech units are 0.1 V).")

    if manual_seconds > 0:
        span = manual.get("position_span")
        if isinstance(span, int):
            if span < 20:
                report.warn(
                    "Manual sample saw almost no encoder movement. "
                    "If you moved the shaft, encoder/gear damage is likely."
                )
            elif span < 300:
                report.warn(
                    "Manual sample saw a small range only. "
                    "If you swept the full free travel, suspect mechanical blockage."
                )
            else:
                report.note(f"Manual encoder span looks alive: {span} raw counts.")

    if active:
        observed = active_result.get("observed_span")
        command = active_result.get("delta_command")
        if isinstance(observed, int) and isinstance(command, int):
            if observed >= max(20, int(command * 0.7)):
                report.note("Small active movement succeeded. Electronics and position loop are probably alive.")
            else:
                report.fail(
                    "Small active movement did not produce enough encoder change. "
                    "Suspect motor output stage, internal gear train, wiring, or a hard mechanical jam."
                )

    if report.ok:
        report.note("No clear electrical failure found in this diagnostic run.")


def _print_human(report: Diagnostic) -> None:
    print(f"Single Feetech motor diagnostic: port={report.port}, id={report.motor_id}, model={report.model}")
    print(f"Result: {'PASS' if report.ok else 'FAIL / SUSPECT'}")
    print()
    for finding in report.findings:
        print(f"- {finding}")
    for warning in report.warnings:
        print(f"- Warning: {warning}")

    ping = report.data.get("ping")
    if ping:
        print()
        print(
            "Ping: "
            f"comm_ok={ping.get('comm_ok')} status_ok={ping.get('status_ok')} "
            f"model_number={ping.get('model_number')} status={ping.get('status_error')}"
        )

    before = report.data.get("snapshot_before") or {}
    after = report.data.get("snapshot_after") or {}
    for label, snap in [("before", before), ("after", after)]:
        if not snap:
            continue
        print()
        print(f"Snapshot {label}:")
        for key in READ_REGISTERS:
            if key in snap:
                print(f"  {key:>22}: {snap[key]}")

    manual = report.data.get("manual_sample")
    if manual:
        print()
        print(
            "Manual sample: "
            f"count={manual.get('count')} span={manual.get('position_span')} "
            f"min={manual.get('position_min')} max={manual.get('position_max')} "
            f"current_max={manual.get('current_max')} load_abs_max={manual.get('load_abs_max')}"
        )

    watch = report.data.get("watch")
    if watch:
        print()
        print(
            "Live watch summary: "
            f"count={watch.get('count')} span={watch.get('position_span')} "
            f"min={watch.get('position_min')} max={watch.get('position_max')}"
        )

    active = report.data.get("active_test")
    if active:
        print()
        print(
            "Active test: "
            f"command_delta={active.get('delta_command')} observed_span={active.get('observed_span')} "
            f"targets={active.get('targets')}"
        )

    scan = report.data.get("scan")
    if scan is not None:
        print()
        print(f"Scan found IDs: {[item.get('id') for item in scan]}")


def run(args: argparse.Namespace) -> Diagnostic:
    port = args.port or _detect_port()
    if not port:
        raise SystemExit("No motor port found. Pass --port /dev/tty.usbmodemXXXX.")

    report = Diagnostic(port=port, motor_id=args.id, model=args.model)
    bus, motor_name = _connect_bus(port, args.id, args.model)
    try:
        if args.scan:
            report.data["scan"] = _scan_ids(bus, args.scan_start, args.scan_end)
            if args.scan_only:
                report.note("Scan-only mode completed.")
                return report

        report.data["ping"] = _ping(bus, args.id)
        report.data["snapshot_before"] = _snapshot(bus, motor_name)

        if args.set_id is not None:
            report.data["set_id"] = _set_motor_id(
                bus,
                motor_name,
                args.id,
                args.set_id,
                assume_yes=args.yes,
            )
            report.note(
                f"Changed motor ID from {args.id} to {args.set_id}. "
                f"Re-run diagnostics with --id {args.set_id}."
            )
            return report

        if args.release or args.open_range or args.manual_seconds > 0 or args.watch or args.active_test:
            _release_for_manual_motion(bus, motor_name)

        if args.open_range:
            report.data["opened_range"] = _open_full_range(bus, motor_name)

        if args.manual_seconds > 0:
            print(
                f"Manual sampling for {args.manual_seconds:.1f}s. Move the motor shaft through its free travel now.",
                flush=True,
            )
            report.data["manual_sample"] = _sample(
                bus,
                motor_name,
                args.manual_seconds,
                args.sample_interval,
            )

        if args.watch:
            report.data["watch"] = _watch(bus, motor_name, args.sample_interval)

        if args.active_test:
            report.data["active_test"] = _active_test(
                bus,
                motor_name,
                args.active_delta,
                args.active_time_ms,
            )

        report.data["snapshot_after"] = _snapshot(bus, motor_name)
    finally:
        _close_bus(bus)

    _classify(report, args.manual_seconds, args.active_test)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="", help="Motor bus serial port. Defaults to lampgo autodetect.")
    parser.add_argument("--id", type=int, default=3, help="Expected motor ID.")
    parser.add_argument("--model", default="sts3215", help="Expected Feetech model key.")
    parser.add_argument("--scan", action="store_true", help="Scan IDs before diagnosis.")
    parser.add_argument("--scan-only", action="store_true", help="Only scan IDs; do not read the requested motor.")
    parser.add_argument("--scan-start", type=int, default=0)
    parser.add_argument("--scan-end", type=int, default=10)
    parser.add_argument("--set-id", type=int, default=None, help="Change the connected single motor's ID.")
    parser.add_argument("--yes", action="store_true", help="Confirm dangerous operations such as --set-id.")
    parser.add_argument("--release", action="store_true", help="Release torque and verify Torque_Enable/Lock.")
    parser.add_argument("--open-range", action="store_true", help="Temporarily set Homing=0 and Min/Max to full range.")
    parser.add_argument(
        "--manual-seconds",
        type=float,
        default=0.0,
        help="Sample encoder while you move the shaft by hand.",
    )
    parser.add_argument("--sample-interval", type=float, default=0.05)
    parser.add_argument("--watch", action="store_true", help="Print live encoder/current readings until Ctrl+C.")
    parser.add_argument("--active-test", action="store_true", help="Run a tiny powered position-loop test.")
    parser.add_argument("--active-delta", type=int, default=64, help="Raw-count delta for active test.")
    parser.add_argument("--active-time-ms", type=int, default=700)
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run(args)
    if args.json:
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(report)
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
