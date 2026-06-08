# Security Policy

YareLampGo is a local-first hardware and AI runtime. Please avoid posting exploit details, credentials, Wi-Fi passwords, private URLs, or device tokens in public issues.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting for this repository:

<https://github.com/ninsmiracle/YareLampGo/security/advisories/new>

If private reporting is not available yet, open a minimal public issue asking for a maintainer contact path, but do not include exploit details or sensitive data.

## Scope

Security-sensitive areas include:

- Web API authentication and local/remote access control.
- OpenClaw plugin token handling.
- LLM, voice, RTC, and provider credential storage.
- ESP32 provisioning and Wi-Fi handling.
- Any behavior that can bypass `MotionRuntime` or `SafetyKernel`.

## Local Secrets

Do not commit `.env`, `lampgo.toml`, `~/.lampgo/config.toml`, `~/.lampgo/credentials.json`, API keys, access tokens, Wi-Fi passwords, internal service URLs, or private hardware identifiers.
