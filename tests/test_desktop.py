"""Tests for DesktopBridge and PermissionSystem."""

from lampgo.bridge.desktop import (
    DesktopAction,
    DesktopBridge,
    PermissionLevel,
    PermissionSystem,
    StubBackend,
)


def test_stub_backend_logs_actions():
    backend = StubBackend()
    backend.mouse_move(10, -5)
    backend.key_press("a")
    assert len(backend.action_log) == 2
    assert "mouse_move(10, -5)" in backend.action_log[0]


def test_permission_denied_by_default():
    perms = PermissionSystem(default_level=PermissionLevel.DENIED)
    assert not perms.is_allowed("mouse_move")


def test_permission_granted():
    perms = PermissionSystem(default_level=PermissionLevel.DENIED)
    perms.grant("mouse_move", PermissionLevel.FULL)
    assert perms.is_allowed("mouse_move")
    assert not perms.is_allowed("key_press")


def test_permission_revoked():
    perms = PermissionSystem()
    perms.grant("mouse_move", PermissionLevel.FULL)
    perms.revoke("mouse_move")
    assert not perms.is_allowed("mouse_move")


def test_bridge_denies_unpermitted_action():
    backend = StubBackend()
    perms = PermissionSystem(default_level=PermissionLevel.DENIED)
    bridge = DesktopBridge(backend=backend, permissions=perms)

    result = bridge.execute_action(DesktopAction(action_type="mouse_move", params={"dx": 10, "dy": 5}))
    assert not result
    assert len(backend.action_log) == 0


def test_bridge_executes_permitted_action():
    backend = StubBackend()
    perms = PermissionSystem()
    perms.grant("mouse_move", PermissionLevel.FULL)
    bridge = DesktopBridge(backend=backend, permissions=perms)

    result = bridge.execute_action(DesktopAction(action_type="mouse_move", params={"dx": 10, "dy": 5}))
    assert result
    assert len(backend.action_log) == 1


def test_bridge_app_launch():
    backend = StubBackend()
    perms = PermissionSystem()
    perms.grant("app_launch", PermissionLevel.FULL)
    bridge = DesktopBridge(backend=backend, permissions=perms)

    result = bridge.execute_action(DesktopAction(action_type="app_launch", params={"app": "calculator"}))
    assert result
    assert "app_launch(calculator)" in backend.action_log[0]
