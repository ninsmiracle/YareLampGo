# YareLampGo Docs

[简体中文](README.md) | English

This index collects public docs for open-source users. YareLampGo turns a robotic desk lamp from lab-style hardware into a desktop companion that developers, creators, and hobbyists can make listen, see, move, and respond.

The public project name is **YareLampGo**. The `lampgo` name remains the internal short name for the CLI command, Python package, and config directory.

The root [README.md](../README.md) / [README.en.md](../README.en.md) is the quick landing page. Longer setup, configuration, motion, Codex, and development notes live under `docs/`.

Translation is being added gradually. Chinese is the default documentation language. English pages use the `README.en.md` filename convention, while deeper docs may remain Chinese until they are high enough priority to translate.

## Getting Started

| Doc | Use it for |
| --- | --- |
| [Quick Start](getting-started/quick-start.md) | First install, Web UI launch, no-hardware mode, and real hardware startup. |
| [Manual V2.0 Setup](getting-started/manual-hardware-setup.en.md) | Servo IDs, S3/C6 flashing, first power, and calibration without Codex. |
| [Configuration](getting-started/configuration.md) | `~/.lampgo/config.toml`, environment variables, credentials, and common config fields. |

## Guides

| Doc | Use it for |
| --- | --- |
| [Motion and Expression](guides/motion-and-expression.md) | Built-in motions, CSV recording/playback, LED expressions, and composed skills. |
| [Codex Integration](guides/codex-integration.md) | Zero-config local Codex handoff and LampGo MCP tools. |
| [V2.0 Hardware and Assembly](hardware/v2/README.en.md) | V2.0 electrical/PCB images, assembly guide, wiring, STEP, and first-power flow. |

## Architecture And Background

| Doc | Use it for |
| --- | --- |
| [Architecture](architecture.md) | IntentRouter, SkillExecutor, MotionRuntime, SafetyKernel, and HAL layering. |
| [Project Description](project_description.md) | Project background, capability boundary, and technical direction. |
| [Roadmap](roadmap.en.md) | Replaceable head modules, algorithm extension points, and future community work. |
| [Composed Skills](composed_skills.md) | User skill / composed skill data format and execution rules. |
| [Hardware And Asset Scope](hardware-and-assets-scope.md) | License boundary for software, firmware, 3D visualization assets, community printable files, and supplier production materials. |
| [Structure Files](../assets/printable/README.en.md) | V2.0 STEP AP214 complete assembly, preview, and fabrication limits. |

## Examples And References

| Path | Contents |
| --- | --- |
| [`examples/basic_motion.py`](../examples/basic_motion.py) | Basic motion control example. |
| [`examples/custom_skill.py`](../examples/custom_skill.py) | Custom skill example. |
| [`lampgo/mcp_stdio.py`](../lampgo/mcp_stdio.py) | Local MCP entrypoint used by Codex to call lamp capabilities. |

## Translation Policy

Chinese is the default documentation language. Default `README.md` files should stay Chinese, and English entry points should use the same-folder `README.en.md` convention. Key onboarding docs should be translated first; deeper design notes can stay in one language until there is enough demand. When changing a bilingual doc, mention in the PR whether the matching translation was updated or is pending.
