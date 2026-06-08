## What Changed

- 

## Verification

- [ ] `uv run pytest`
- [ ] I ran `uv run ruff check lampgo tests` or noted existing lint debt that is unrelated to this PR.
- [ ] I tested `--no-hw` mode when this affects app startup, Web UI, CLI, or skills.
- [ ] I documented hardware, device, serial port, calibration file, and motion effect when this affects real hardware.

## Notes

- This PR does not include `.env`, `credentials.json`, private tokens, internal service URLs, or unauthorized assets.
