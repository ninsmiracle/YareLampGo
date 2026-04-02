#!/usr/bin/env python3
"""Interactive joint direction verification script.

Connects to the running lampgo daemon via IPC, then tests each joint
one by one: moves it to a small positive offset, pauses for observation,
moves it to a small negative offset, pauses, then returns to origin.

Usage (daemon must be running):
    uv run python tests/test_joint_directions.py
"""

import json
import sys
import time

from lampgo.ipc import ipc_send, is_daemon_running

JOINTS = [
    {
        "name": "base_yaw",
        "offset": 25,
        "positive_label": "右转 (turn right)",
        "negative_label": "左转 (turn left)",
        "description": "底座水平旋转",
    },
    {
        "name": "base_pitch",
        "offset": 20,
        "positive_label": "前倾/低头 (tilt forward / look down)",
        "negative_label": "后仰/抬头 (tilt backward / look up)",
        "description": "底座前后俯仰",
    },
    {
        "name": "elbow_pitch",
        "offset": 20,
        "positive_label": "肘弯曲/灯头下降 (bend elbow / lamp head lower)",
        "negative_label": "肘伸展/灯头上升 (extend elbow / lamp head raise)",
        "description": "肘关节弯曲",
    },
    {
        "name": "wrist_roll",
        "offset": 20,
        "positive_label": "顺时针 (clockwise, viewed from above)",
        "negative_label": "逆时针 (counter-clockwise)",
        "description": "腕部旋转",
    },
    {
        "name": "wrist_pitch",
        "offset": 20,
        "positive_label": "灯头低头 (lamp head tilt down)",
        "negative_label": "灯头抬头 (lamp head tilt up)",
        "description": "灯头俯仰",
    },
]


def invoke(skill_id: str, params: dict) -> dict:
    return ipc_send({"cmd": "invoke", "skill_id": skill_id, "params": params, "wait": True})


def get_positions() -> dict[str, float]:
    resp = ipc_send({"cmd": "status"})
    return resp.get("result", {}).get("joint_positions", {})


def move_joint(name: str, value: float, velocity: float = 40.0) -> None:
    invoke("move_to", {name: value, "velocity": velocity})


def main() -> None:
    if not is_daemon_running():
        print("错误: lampgo daemon 未运行。请先执行 `uv run lampgo run`")
        sys.exit(1)

    positions = get_positions()
    if not positions:
        print("错误: 无法读取关节位置")
        sys.exit(1)

    print("=" * 60)
    print("  lampgo 关节方向验证工具")
    print("=" * 60)
    print()
    print("当前关节位置:")
    for k, v in positions.items():
        print(f"  {k:>14s} = {v:7.1f}°")
    print()
    print("测试流程: 对每个关节，先移到 +offset（正方向），")
    print("再移到 -offset（负方向），最后回到原位。")
    print("请观察实际运动方向是否与预期一致。")
    print()

    results: list[dict] = []

    for joint in JOINTS:
        name = joint["name"]
        offset = joint["offset"]
        origin = positions.get(name)
        if origin is None:
            print(f"  跳过 {name}（未在当前位置中找到）")
            continue

        print("-" * 60)
        print(f"测试关节: {name}  ({joint['description']})")
        print(f"  当前位置: {origin:.1f}°")
        print()

        # --- Positive direction ---
        target_pos = origin + offset
        print(f"  [1/2] 移到 {target_pos:.1f}°（正方向 +{offset}°）")
        print(f"    预期运动: {joint['positive_label']}")
        input("    按 Enter 开始...")
        move_joint(name, target_pos)
        time.sleep(0.8)

        # --- Negative direction ---
        target_neg = origin - offset
        print(f"  [2/2] 移到 {target_neg:.1f}°（负方向 -{offset}°）")
        print(f"    预期运动: {joint['negative_label']}")
        input("    按 Enter 开始...")
        move_joint(name, target_neg)
        time.sleep(0.8)

        # --- Return to origin ---
        print(f"  回到原位 {origin:.1f}°...")
        move_joint(name, origin)
        time.sleep(0.5)
        print()

        answer = input(f"  {name} 实际方向与预期一致吗? (y/n/skip): ").strip().lower()
        if answer == "y":
            results.append({"joint": name, "status": "✓ 正确"})
            print(f"  → {name}: 方向正确 ✓")
        elif answer == "n":
            note = input("  请描述实际观察到的方向: ").strip()
            results.append({"joint": name, "status": "✗ 反向", "note": note})
            print(f"  → {name}: 方向有误 ✗")
        else:
            results.append({"joint": name, "status": "- 跳过"})
            print(f"  → {name}: 已跳过")
        print()

    print("=" * 60)
    print("  验证结果汇总")
    print("=" * 60)
    for r in results:
        line = f"  {r['joint']:>14s}  {r['status']}"
        if r.get("note"):
            line += f"  ({r['note']})"
        print(line)
    print()

    wrong = [r for r in results if "反向" in r["status"]]
    if wrong:
        print("以下关节方向需要修正 joints.md 定义:")
        for r in wrong:
            print(f"  - {r['joint']}: {r.get('note', '')}")
    else:
        print("所有测试的关节方向均正确！")
    print()


if __name__ == "__main__":
    main()
