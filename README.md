# lampgo

Desktop embodied intelligent lamp robot runtime.

Clean, safe, OpenClaw-ready Python package for controlling the LeLamp robotic arm with smooth motion, LED expressions, and extensible skills.

## Quick Start

```bash
# Install
uv sync

# List available skills
uv run lampgo skills

# Start the server
uv run lampgo run --motor-port /dev/ttyUSB0

# Move the arm
uv run lampgo move --motor-port /dev/ttyUSB0 base_yaw=30 base_pitch=-20

# Play a recording
uv run lampgo play --motor-port /dev/ttyUSB0 nod
```

## Development

```bash
# Install with dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Lint
uv run ruff check lampgo/ tests/
```

## Architecture

See `docs/architecture.md` for the full design document.

### Key modules

- `lampgo.core.hal` — Hardware abstraction (Feetech motors via lerobot)
- `lampgo.core.safety` — Safety kernel (joint limits, velocity caps, e-stop)
- `lampgo.core.motion` — Trapezoidal velocity motion runtime (dedicated control thread)
- `lampgo.core.led` — ESP32 LED controller
- `lampgo.skills` — Skill system (base class, registry, executor, FSM)
- `lampgo.bridge.openclaw` — OpenClaw adapter

## License

Apache-2.0
