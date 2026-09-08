"""Replay-resistant authentication helpers for ESP32-P4 LAN transports.

The paired secret never crosses the normal P4 control, media, or asset
connections.  Firmware stores only its SHA-256 digest; that digest becomes the
HMAC key for a short-lived, device-issued challenge.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lampgo.device.esp32 import Esp32DeviceManager


AUTH_DOMAIN = "lampgo-p4-auth-v1"


def build_p4_auth_proof(*, owner_id: str, pairing_secret: str, purpose: str, nonce: str) -> str:
    """Return the protocol-v1 HMAC proof for one device-issued nonce."""
    if not owner_id or not pairing_secret or not purpose or not nonce:
        raise ValueError("P4 authentication requires owner, secret, purpose, and nonce")
    key = hashlib.sha256(pairing_secret.encode("utf-8")).hexdigest().encode("ascii")
    message = "\n".join((AUTH_DOMAIN, purpose, owner_id, nonce)).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def build_p4_auth_fields(*, owner_id: str, pairing_secret: str, purpose: str, nonce: str) -> dict[str, str]:
    """Build the non-secret fields accepted by P4 firmware."""
    return {
        "owner_id": owner_id,
        "auth_purpose": purpose,
        "auth_nonce": nonce,
        "auth_proof": build_p4_auth_proof(
            owner_id=owner_id,
            pairing_secret=pairing_secret,
            purpose=purpose,
            nonce=nonce,
        ),
    }


async def authenticate_p4_websocket(ws: Any, manager: Esp32DeviceManager, *, purpose: str) -> None:
    """Complete a P4 WebSocket challenge-response exchange.

    The first frame intentionally contains no credential.  The device returns
    a one-time nonce, and only then does the backend send an HMAC proof.
    """
    await ws.send(json.dumps({"type": "auth_init", "purpose": purpose}, separators=(",", ":")))
    raw_challenge = await asyncio.wait_for(ws.recv(), timeout=5.0)
    if isinstance(raw_challenge, bytes):
        raw_challenge = raw_challenge.decode("utf-8", errors="strict")
    try:
        challenge = json.loads(raw_challenge)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConnectionError("P4 returned an invalid authentication challenge") from exc
    nonce = str(challenge.get("nonce") or "")
    if challenge.get("type") != "challenge" or challenge.get("purpose") != purpose or not nonce:
        raise ConnectionError("P4 rejected the authentication challenge request")
    fields = build_p4_auth_fields(
        owner_id=manager.owner_id,
        pairing_secret=manager.pairing_secret,
        purpose=purpose,
        nonce=nonce,
    )
    await ws.send(json.dumps({"type": "auth", **fields}, separators=(",", ":")))
