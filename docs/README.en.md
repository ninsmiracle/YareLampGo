# YareLampGo Docs

[简体中文](README.md) | English

This index collects public docs for open-source users. YareLampGo turns a robotic desk lamp from lab-style hardware into a desktop companion that developers, creators, and hobbyists can make listen, see, move, and respond.

The public project name is **YareLampGo**. The `lampgo` name remains the internal short name for the CLI command, Python package, config directory, and OpenClaw plugin identifiers.

The root [README.md](../README.md) / [README.en.md](../README.en.md) is the quick landing page. Longer setup, configuration, motion, OpenClaw, and development notes live under `docs/`.

Translation is being added gradually. Chinese is the default documentation language. English pages use the `README.en.md` filename convention, while deeper docs may remain Chinese until they are high enough priority to translate.

## Getting Started

| Doc | Use it for |
| --- | --- |
| [Quick Start](getting-started/quick-start.md) | First install, Web UI launch, no-hardware mode, and real hardware startup. |
| [Configuration](getting-started/configuration.md) | `~/.lampgo/config.toml`, environment variables, credentials, and common config fields. |

## Guides

| Doc | Use it for |
| --- | --- |
| [Motion and Expression](guides/motion-and-expression.md) | Built-in motions, CSV recording/playback, LED expressions, and composed skills. |
| [OpenClaw Integration](guides/openclaw-integration.md) | Connect YareLampGo to OpenClaw so Agents can call lamp capabilities. |
| [Public Hardware Docs](hardware/README.en.md) | Public component photos, wiring diagram, wiring table, and printable structure entry points. |

## Architecture And Background

| Doc | Use it for |
| --- | --- |
| [Architecture](architecture.md) | IntentRouter, SkillExecutor, MotionRuntime, SafetyKernel, and HAL layering. |
| [Project Description](project_description.md) | Project background, capability boundary, and technical direction. |
| [Composed Skills](composed_skills.md) | User skill / composed skill data format and execution rules. |
| [Hardware And Asset Scope](hardware-and-assets-scope.md) | License boundary for software, firmware, 3D visualization assets, community printable files, and supplier production materials. |
| [Printable Structure Files](../assets/printable/README.en.md) | V1.0 STEP/STP appearance and structural files, preview images, and print plate images. |

## Examples And References

| Path | Contents |
| --- | --- |
| [`examples/basic_motion.py`](../examples/basic_motion.py) | Basic motion control example. |
| [`examples/custom_skill.py`](../examples/custom_skill.py) | Custom skill example. |
| [`examples/openclaw_integration.py`](../examples/openclaw_integration.py) | OpenClaw integration example. |
| [`openclaw-skills/lampgo/references/`](../openclaw-skills/lampgo/references/) | Agent-readable joint, motion, LED, and API references. |

## Translation Policy

Chinese is the default documentation language. Default `README.md` files should stay Chinese, and English entry points should use the same-folder `README.en.md` convention. Key onboarding docs should be translated first; deeper design notes can stay in one language until there is enough demand. When changing a bilingual doc, mention in the PR whether the matching translation was updated or is pending.
