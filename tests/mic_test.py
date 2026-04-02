"""Quick mic diagnostic — print RMS energy for each chunk.

Usage:
    uv run python tests/mic_test.py [device_index]

Speak into the mic and watch the RMS values.
Silence should be < 50, speech should be > 200.
"""

import struct
import sys
import time


def rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


def main():
    import sounddevice as sd

    print("可用输入设备:")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}  (通道: {d['max_input_channels']}, 采样率: {int(d['default_samplerate'])})")
    print()

    device = int(sys.argv[1]) if len(sys.argv) > 1 else None

    if device is not None:
        info = sd.query_devices(device)
        if info["max_input_channels"] == 0:
            print(f"错误: 设备 [{device}] '{info['name']}' 不是输入设备，请选择上面列出的设备编号")
            sys.exit(1)
    else:
        info = sd.query_devices(kind="input")
        print("未指定设备，使用系统默认输入")

    print(f"Device: {info['name']} (index={device})")
    print(f"Max input channels: {info['max_input_channels']}")
    print()
    print("Listening... Speak into the mic. Ctrl+C to stop.")
    print(f"{'Time':>6}  {'RMS':>8}  {'Bar'}")
    print("-" * 60)

    sample_rate = 16000
    chunk_ms = 30
    blocksize = int(sample_rate * chunk_ms / 1000)

    peak_rms = 0.0
    frame = 0

    def callback(indata, frames, time_info, status):
        nonlocal peak_rms, frame
        frame += 1
        energy = rms(bytes(indata))
        peak_rms = max(peak_rms, energy)

        if frame % 10 == 0:
            bar_len = int(min(energy / 100, 50))
            bar = "█" * bar_len
            t = f"{frame * chunk_ms / 1000:.1f}s"
            print(f"{t:>6}  {energy:8.1f}  {bar}")

    stream = sd.RawInputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=blocksize,
        device=device,
        callback=callback,
    )

    try:
        stream.start()
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
        print(f"\nPeak RMS: {peak_rms:.1f}")


if __name__ == "__main__":
    main()
