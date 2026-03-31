#!/usr/bin/env python3
"""Test wrist_roll direction only.

Moves wrist_roll from safe position to +20, pauses, then to -20, pauses,
then returns. Observe which direction is clockwise vs counter-clockwise.

Usage (daemon must be running):
    uv run python scripts/test_wrist_roll.py
"""

import sys
import time

from lampgo.ipc import ipc_send, is_daemon_running


def invoke_wait(skill_id: str, params: dict) -> dict:
    return ipc_send({"cmd": "invoke", "skill_id": skill_id, "params": params, "wait": True})


def get_positions() -> dict[str, float]:
    return ipc_send({"cmd": "status"}).get("result", {}).get("joint_positions", {})


def main() -> None:
    if not is_daemon_running():
        print("错误: daemon 未运行")
        sys.exit(1)

    pos = get_positions()
    origin = pos.get("wrist_roll", 0.0)
    print(f"wrist_roll 当前位置: {origin:.1f}°")
    print()

    # Positive
    target = origin + 25
    print(f"[1] 移到 {target:.1f}°（正方向 +25°）")
    print(f"    joints.md 预期: 顺时针 (clockwise)")
    input("    按 Enter 开始...")
    invoke_wait("move_to", {"wrist_roll": target, "velocity": 30.0})
    time.sleep(1.0)

    # Negative
    target = origin - 25
    print(f"[2] 移到 {target:.1f}°（负方向 -25°）")
    print(f"    joints.md 预期: 逆时针 (counter-clockwise)")
    input("    按 Enter 开始...")
    invoke_wait("move_to", {"wrist_roll": target, "velocity": 30.0})
    time.sleep(1.0)

    # Return
    print(f"[3] 回到原位 {origin:.1f}°...")
    invoke_wait("move_to", {"wrist_roll": origin, "velocity": 30.0})
    time.sleep(0.5)

    print()
    answer = input("wrist_roll 方向与预期一致吗? (y/n): ").strip().lower()
    if answer == "y":
        print("→ wrist_roll: 方向正确 ✓")
    else:
        note = input("请描述实际观察到的方向: ").strip()
        print(f"→ wrist_roll: 方向有误 ✗  ({note})")


if __name__ == "__main__":
    main()
