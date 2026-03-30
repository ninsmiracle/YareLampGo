#!/usr/bin/env python3
"""Demo: cycle through reference poses for visual verification."""

import asyncio
import httpx

BASE = "http://localhost:8420"
PAUSE = 5
VEL = 40

POSES = [
    ("1. 安全位 (idle)",        {"base_yaw": 0, "base_pitch": -45, "elbow_pitch": 83,  "wrist_pitch": 3,   "wrist_roll": 0, "velocity": VEL}),
    ("2. 站直 (stand tall)",    {"base_yaw": 0, "base_pitch": 0,   "elbow_pitch": -85, "wrist_pitch": 30,  "wrist_roll": 0, "velocity": VEL}),
    ("3. 看桌面 (look at desk)",{"base_yaw": 0, "base_pitch": -10, "elbow_pitch": 25,  "wrist_pitch": 90,  "wrist_roll": 0, "velocity": VEL}),
    ("4. 前倾 (lean forward)",  {"base_yaw": 0, "base_pitch": 65,  "elbow_pitch": -70, "wrist_pitch": 50,  "wrist_roll": 0, "velocity": VEL}),
    ("5. 后仰 (lean backward)", {"base_yaw": 0, "base_pitch": -98, "elbow_pitch": -11, "wrist_pitch": 100, "wrist_roll": 0, "velocity": VEL}),
    ("6. 回安全位",             {"base_yaw": 0, "base_pitch": -45, "elbow_pitch": 83,  "wrist_pitch": 3,   "wrist_roll": 0, "velocity": VEL}),
]


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        for name, params in POSES:
            print(f"\n{'='*40}")
            print(f"  {name}")
            print(f"  bp={params['base_pitch']}, ep={params['elbow_pitch']}, wp={params['wrist_pitch']}")
            print(f"{'='*40}")

            resp = await client.post(
                f"{BASE}/api/invoke",
                json={"skill_id": "move_to", "params": params, "wait": True},
            )
            data = resp.json()
            print(f"  result: {data.get('status', 'unknown')}")
            print(f"  holding {PAUSE}s...")
            await asyncio.sleep(PAUSE)

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
