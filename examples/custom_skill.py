"""How to write a custom skill for lampgo.

A skill is a Python class that inherits from Skill and implements execute().
Register it with the SkillRegistry and it's automatically available to
the CLI, OpenClaw, and any other caller.
"""

import asyncio
import argparse
from typing import Any

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.core.types import MotionTarget, SkillResult
from lampgo.server import LampgoServer
from lampgo.skills.base import ParameterSpec, Skill, SkillContext


class PeekAbooSkill(Skill):
    skill_id = "peek_a_boo"
    description = "Hide behind the arm, then peek out!"
    parameters = {
        "hide_pitch": ParameterSpec(name="hide_pitch", type="float", required=False, default=-60.0),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        hide_pitch = float(params.get("hide_pitch", -60.0))

        # Hide
        ctx.led.set_mode("sleep")
        done = ctx.motion.move_to(MotionTarget(joints={"base_pitch": hide_pitch}))
        while not done.is_set():
            await asyncio.sleep(0.03)

        await asyncio.sleep(1.0)

        # Peek!
        ctx.led.set_mode("surprised")
        done = ctx.motion.move_to(MotionTarget(joints={"base_pitch": 10.0}, max_velocity=200.0))
        while not done.is_set():
            await asyncio.sleep(0.03)

        await asyncio.sleep(0.5)
        ctx.led.set_mode("smiley")
        return SkillResult(status="ok")


async def main(motor_port: str) -> None:
    config = LampgoConfig(device=DeviceConfig(motor_port=motor_port))
    server = LampgoServer(config)
    server.registry.register(PeekAbooSkill())  # register custom skill
    await server.start()

    ctx = server.make_context()
    result = await server.executor.invoke("peek_a_boo", ctx)
    print(f"Result: {result.status}")

    # Show that OpenClaw can see it
    caps = server.openclaw.list_capabilities_dict()
    print(f"OpenClaw sees {len(caps)} skills: {[c['skill_id'] for c in caps]}")

    await server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--motor-port", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.motor_port))
