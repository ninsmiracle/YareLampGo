"""5 lines to make the lamp nod.

Usage:
    uv run python examples/basic_motion.py --motor-port /dev/ttyUSB0
"""

import asyncio
import argparse

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer


async def main(motor_port: str) -> None:
    config = LampgoConfig(device=DeviceConfig(motor_port=motor_port))
    server = LampgoServer(config)
    await server.start()

    ctx = server.make_context()
    result = await server.executor.invoke("nod", ctx, amplitude=20, count=3)
    print(f"Result: {result.status}")

    await server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--motor-port", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.motor_port))
