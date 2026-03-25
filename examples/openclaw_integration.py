"""How OpenClaw interacts with lampgo.

This example shows how an OpenClaw agent would:
1. List available capabilities
2. Invoke a skill with parameters
3. Handle the result
"""

import asyncio
import argparse

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer


async def main(motor_port: str) -> None:
    config = LampgoConfig(device=DeviceConfig(motor_port=motor_port))
    server = LampgoServer(config)
    await server.start()

    # 1. List capabilities (what an OpenClaw agent sees)
    capabilities = server.openclaw.list_capabilities_dict()
    print("Available capabilities:")
    for cap in capabilities:
        print(f"  {cap['skill_id']}: {cap['description']}")
        if cap["parameters"]:
            for pname, pspec in cap["parameters"].items():
                print(f"    - {pname} ({pspec['type']}): {pspec['description']}")

    # 2. Invoke a skill
    ctx = server.make_context()
    print("\nInvoking 'nod' skill...")
    result = await server.openclaw.invoke("nod", {"amplitude": 15, "count": 2}, ctx)
    print(f"Result: status={result.status}, data={result.result}")

    # 3. Invoke a non-existent skill
    result = await server.openclaw.invoke("fly_to_moon", {}, ctx)
    print(f"Unknown skill: status={result.status}, error={result.error_code}")

    await server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--motor-port", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.motor_port))
