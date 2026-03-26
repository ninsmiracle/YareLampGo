"""Server status response contract tests."""

from __future__ import annotations

import json

import pytest

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer


@pytest.mark.asyncio
async def test_status_response_is_json_serializable():
    """Status payload should only contain JSON-serializable primitives."""
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    response = await server.handle_request({"cmd": "status"})

    assert response["ok"] is True
    assert isinstance(response["result"]["estopped"], bool)
    json.dumps(response)
