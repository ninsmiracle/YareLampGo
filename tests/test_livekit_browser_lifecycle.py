from pathlib import Path

APP_JS = Path("lampgo/web/static/app.js")


def _source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _section(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    return source[start_index : source.index(end, start_index)]


def test_normal_livekit_hangup_waits_before_cleanup() -> None:
    source = _source()
    stop = _section(
        source,
        '  function stopBrowserLiveKitCall(reason = "manual", options = {}) {',
        '  async function notifyLiveKitRoomEnded',
    )

    disconnect_index = stop.index("await disconnectBrowserLiveKitRoom(room);")
    notify_index = stop.index("await notifyLiveKitRoomEnded(roomName, reason, clientCallId);")
    finally_index = stop.index("})().finally(() => {")
    cleanup_index = stop.index("cleanupBrowserLiveKitCall();", finally_index)

    assert disconnect_index < notify_index < finally_index < cleanup_index
    assert "if (browserCallHangupPromise) return browserCallHangupPromise;" in stop
    assert 'setBrowserCallState("leaving");' in stop


def test_livekit_disconnect_event_does_not_duplicate_active_hangup() -> None:
    source = _source()
    event_handler = _section(
        source,
        '        room.on("disconnected", () => {',
        "        await room.connect(serverUrl, token);",
    )

    assert "browserCallDisconnectingRoom === room" in event_handler
    assert "browserCallDisconnectPromises.has(room)" in event_handler
    assert 'stopBrowserLiveKitCall("livekit_disconnected", {' in event_handler
    assert "alreadyDisconnected: true" in event_handler


def test_livekit_start_can_be_cancelled_during_each_async_stage() -> None:
    source = _source()
    start = _section(
        source,
        "  async function startBrowserLiveKitCall(options = {}) {",
        "  async function scheduleHangupAfterTtsPlayout() {",
    )

    assert "browserCallJoiningRoom = room;" in start
    assert start.count("throwIfBrowserCallStopRequested();") >= 4
    assert "await disconnectBrowserLiveKitRoom(room);" in start
    assert 'cancelled ? "start_cancelled" : "start_failed"' in start


def test_livekit_room_end_uses_awaitable_fetch_and_beacon_only_for_page_exit() -> None:
    source = _source()
    notify = _section(
        source,
        "  async function notifyLiveKitRoomEnded",
        "  function endBrowserLiveKitCallForPageExit() {",
    )
    page_exit = _section(
        source,
        "  function endBrowserLiveKitCallForPageExit() {",
        "  function createCallSession() {",
    )

    assert "if (options.preferBeacon)" in notify
    assert 'const response = await fetch("/api/livekit/room/end"' in notify
    assert "CALL_END_NOTIFY_TIMEOUT_MS" in notify
    assert "room.disconnect();" in page_exit
    assert "{ preferBeacon: true }" in page_exit
    assert 'window.addEventListener("pagehide", endBrowserLiveKitCallForPageExit);' in page_exit


def test_all_non_unload_room_disconnects_use_the_serialized_helper() -> None:
    source = _source()

    # One call lives in the serialized helper; the other is the unavoidable
    # synchronous best-effort path while the document is unloading.
    assert source.count("room.disconnect()") == 2
    assert "const browserCallDisconnectPromises = new WeakMap();" in source
