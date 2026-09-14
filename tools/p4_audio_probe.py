"""Bounded P4 audio checks; report levels, never record microphone content."""

import argparse
import asyncio
import contextlib
import json
import math
import os
import struct
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import websockets

from lampgo.device.audio_stream import P4SpeakerStream, acknowledge_audio_frame
from lampgo.device.p4_auth import authenticate_p4_websocket

ARGS = None
START = time.monotonic()


def log(event, **fields):
    print(json.dumps(dict(t=round(time.monotonic() - START, 2), event=event, **fields)), flush=True)


async def main():
    speech_pcm = b""
    if ARGS.speech_wav:
        with wave.open(ARGS.speech_wav, "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                raise ValueError("speech WAV must be PCM16 mono, 16 kHz")
            speech_pcm = wav.readframes(wav.getnframes())
    config_dir = Path(os.environ.get("LAMPGO_HOME", Path.home() / ".lampgo"))
    manager = SimpleNamespace(
        owner_id=(config_dir / "esp32_owner_id").read_text().strip(),
        pairing_secret=(config_dir / "esp32_pairing_secret").read_text().strip(),
    )
    async with httpx.AsyncClient(trust_env=False, timeout=3.0) as http:

        async def status():
            r = await http.get(f"http://{ARGS.host}/device/status", params={"audio_diagnostics": "1"})
            r.raise_for_status()
            s = r.json()
            return {
                k: s.get(k)
                for k in ("firmware", "rssi", "hosted", "audio", "uptime_ms", "reset_reason", "wifi_power_save")
            }

        async def ws_open(purpose):
            if purpose == "speaker" and ARGS.backend:
                from lampgo.personastore import get_local_api_token

                ws = await websockets.connect(
                    ARGS.backend.replace("http://", "ws://", 1).replace("https://", "wss://", 1).rstrip("/") + "/api/device/speaker",
                    additional_headers={"Authorization": "Bearer " + get_local_api_token()},
                    proxy=None, ping_interval=None, close_timeout=1, open_timeout=5, compression=None,
                )
                ws.audio_flow = False  # Backend negotiates the device-side credits.
                ws.speaker_stream = P4SpeakerStream(ws, False)
                return ws
            ws = await websockets.connect(
                f"ws://{ARGS.host}:81/ws/{purpose}",
                proxy=None,
                ping_interval=None,
                close_timeout=1,
                open_timeout=5,
                compression=None,
            )
            try:
                ws.audio_flow = await authenticate_p4_websocket(ws, manager, purpose="ws:" + purpose)
                ws.speaker_stream = P4SpeakerStream(ws, ws.audio_flow)
            except BaseException:
                await ws.close()
                raise
            return ws

        for phase in ARGS.phases.split(","):
            before = await status()
            if not str(before.get("firmware", "")).startswith("p4-head-"):
                raise RuntimeError("Refusing audio test on a non-P4 target")
            log("start", phase=phase, status=before)
            mic = speaker = task = None
            mic_frames = mic_bytes = mic_peak = sent = 0
            tone_levels = []

            async def receive():
                nonlocal mic_frames, mic_bytes, mic_peak
                async for frame in mic:
                    if isinstance(frame, bytes):
                        mic_frames += 1
                        mic_bytes += len(frame)
                        vals = struct.unpack("<" + "h" * (len(frame) // 2), frame)
                        mic_peak = max(mic_peak, max(map(abs, vals), default=0))
                        if phase == "tone":
                            re = sum(v * math.cos(2 * math.pi * 440 * i / 16000) for i, v in enumerate(vals))
                            im = sum(v * math.sin(2 * math.pi * 440 * i / 16000) for i, v in enumerate(vals))
                            tone_levels.append((time.monotonic(), 2 * math.hypot(re, im) / len(vals)))
                        await acknowledge_audio_frame(mic, mic.audio_flow)

            async def poll():
                while True:
                    await asyncio.sleep(3)
                    log("status", phase=phase, received=mic_frames, sent=sent, status=await status())

            poll_task = None
            try:
                initial_sent = (await status())["audio"]["ws_frames_sent"]
                if phase in ("mic", "duplex", "tone", "speech", "stalled"):
                    mic = await ws_open("audio")
                    if phase == "stalled":
                        if not mic.audio_flow:
                            raise RuntimeError("Stall test requires bounded ack-v1 firmware")
                        mic.transport.pause_reading()
                    else:
                        task = asyncio.create_task(receive())
                if phase in ("speaker", "duplex", "tone", "speech", "stalled"):
                    speaker = await ws_open("speaker")
                poll_task = asyncio.create_task(poll())
                deadline = time.monotonic() + ARGS.seconds
                next_frame = time.monotonic()
                while time.monotonic() < deadline:
                    if poll_task.done():
                        await poll_task
                    if task is not None and task.done():
                        await task
                        raise RuntimeError("mic closed")
                    if speaker:
                        # One second of gentle tone followed by four seconds of silence.
                        tone = phase == "tone" and (sent * 0.06) % 5 < 1
                        pcm = struct.pack(
                            "<960h",
                            *[int(5000 * math.sin(2 * math.pi * 440 * i / 16000)) if tone else 0 for i in range(960)],
                        )
                        if phase == "speech":
                            pcm = speech_pcm[sent * 1920 : (sent + 1) * 1920].ljust(1920, b"\0")
                        await speaker.speaker_stream.send(pcm)
                        sent += 1
                    next_frame = max(next_frame + 0.06, time.monotonic())
                    await asyncio.sleep(max(0, next_frame - time.monotonic()))
                if phase == "stalled":
                    stalled_status = await status()
                    assert (
                        stalled_status["audio"]["ws_frames_sent"] - initial_sent
                        <= stalled_status["audio"]["mic_window_frames"]
                    ), "unbounded mic window"
                    mic.transport.resume_reading()
                    task = asyncio.create_task(receive())
                    await asyncio.sleep(3)
                    assert mic_frames > 20, "microphone did not recover after stall"
                after = await status()
                if mic:
                    assert mic_bytes > 0, "no microphone PCM received"
                if speaker:
                    assert after["audio"]["speaker_bytes_written"] > before["audio"]["speaker_bytes_written"]
                    assert after["audio"]["speaker_write_failures"] == before["audio"]["speaker_write_failures"]
                    if "speaker_dma_blocks" in after["audio"]:
                        assert after["audio"]["speaker_diag_error"] == "ESP_OK", "DMA diagnostics unavailable"
                        assert after["audio"]["speaker_dma_blocks"] > before["audio"]["speaker_dma_blocks"], "I2S DMA stopped"
                        assert after["audio"]["speaker_dma_last_age_ms"] < 100, "I2S DMA completion stalled"
                        if phase in ("tone", "speech"):
                            assert after["audio"]["speaker_dma_nonzero_samples"] > before["audio"]["speaker_dma_nonzero_samples"], "tone never reached completed DMA buffers"
                assert after["uptime_ms"] >= before["uptime_ms"], "P4 rebooted during audio test"
                log(
                    "pass",
                    phase=phase,
                    sent=sent,
                    mic_frames=mic_frames,
                    mic_bytes=mic_bytes,
                    mic_peak=mic_peak,
                    tone_440_peak=round(max((v for _, v in tone_levels), default=0), 2),
                    status=after,
                )
            finally:
                for t in (poll_task, task):
                    if t:
                        t.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await t
                for ws in (speaker, mic):
                    if ws:
                        ws.transport.resume_reading()
                        await ws.close()
            await asyncio.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Paired P4 IP or hostname; close existing calls first")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--backend", help="Optional running backend URL for the speaker relay; microphone stays direct")
    parser.add_argument("--speech-wav", help="Known nonsensitive PCM16/16 kHz mono WAV for the speech phase")
    parser.add_argument(
        "--phases",
        default="stalled,mic,duplex",
        help="Comma-separated stalled,mic,speaker,duplex,tone,speech; tone emits quiet test beeps",
    )
    ARGS = parser.parse_args()
    if not 5 <= ARGS.seconds <= 600:
        parser.error("--seconds must be between 5 and 600")
    if not set(ARGS.phases.split(",")) <= {"stalled", "mic", "speaker", "duplex", "tone", "speech"}:
        parser.error("unknown audio test phase")
    if "speech" in ARGS.phases.split(",") and not ARGS.speech_wav:
        parser.error("speech phase requires --speech-wav")
    try:
        asyncio.run(main())
    except Exception as exc:
        import traceback

        traceback.print_exc()
        log("FAIL", kind=type(exc).__name__, error=str(exc))
        raise SystemExit(1)
