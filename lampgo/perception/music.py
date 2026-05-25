"""Music feature extraction and beat gating for audio-reactive motion."""

from __future__ import annotations

import asyncio
import math
import os
import platform
import queue
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class MusicFeatures:
    timestamp: float
    rms: float
    bass_energy: float
    mid_energy: float
    treble_energy: float
    onset_score: float
    bpm_estimate: float = 0.0

    @property
    def is_silence(self) -> bool:
        return self.rms < 0.01


@dataclass(frozen=True)
class BeatDecision:
    accent: bool
    beat_index: int
    beat_stride: int
    intensity: float
    bpm_estimate: float
    reason: str = ""
    timestamp: float = 0.0


class AudioSourceError(RuntimeError):
    """Raised when an audio source cannot start or provide PCM data."""


class MusicAudioSource(Protocol):
    sample_rate: int
    channels: int

    async def start(self) -> None: ...

    async def read_chunk(self) -> bytes | None: ...

    async def stop(self) -> None: ...


class MusicFeatureExtractor:
    """Convert PCM16LE frames into compact music features.

    The extractor intentionally keeps the model simple: short non-overlapping
    FFT windows, three broad energy bands, and spectral-flux onset detection.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        window_ms: int = 100,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = max(1, int(channels))
        self.window_samples = max(128, int(self.sample_rate * window_ms / 1000))
        self._buffer = np.empty(0, dtype=np.float32)
        self._prev_mag: np.ndarray | None = None
        self._onset_floor = 0.05
        self._rms_floor = 0.02
        self._bass_floor = 0.002

    def push_pcm(self, pcm: bytes, *, timestamp: float | None = None) -> list[MusicFeatures]:
        samples = self._decode_pcm16(pcm)
        if samples.size == 0:
            return []
        self._buffer = np.concatenate((self._buffer, samples))

        out: list[MusicFeatures] = []
        now = time.monotonic() if timestamp is None else float(timestamp)
        while self._buffer.size >= self.window_samples:
            frame = self._buffer[: self.window_samples]
            self._buffer = self._buffer[self.window_samples :]
            out.append(self._features_for_frame(frame, now))
            now += self.window_samples / self.sample_rate
        return out

    def _decode_pcm16(self, pcm: bytes) -> np.ndarray:
        if len(pcm) < 2:
            return np.empty(0, dtype=np.float32)
        usable = len(pcm) - (len(pcm) % (2 * self.channels))
        if usable <= 0:
            return np.empty(0, dtype=np.float32)
        arr = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0
        if self.channels > 1:
            arr = arr.reshape(-1, self.channels).mean(axis=1)
        return arr

    def _features_for_frame(self, frame: np.ndarray, timestamp: float) -> MusicFeatures:
        frame = frame.astype(np.float32, copy=False)
        if frame.size == 0:
            return MusicFeatures(timestamp, 0.0, 0.0, 0.0, 0.0, 0.0)

        rms = float(np.sqrt(np.mean(np.square(frame))))
        window = np.hanning(frame.size).astype(np.float32)
        mag = np.abs(np.fft.rfft(frame * window)) / max(frame.size / 2.0, 1.0)
        freqs = np.fft.rfftfreq(frame.size, d=1.0 / self.sample_rate)

        bass = self._band_level(freqs, mag, 40.0, 160.0)
        mid = self._band_level(freqs, mag, 160.0, 2000.0)
        treble = self._band_level(freqs, mag, 2000.0, min(8000.0, self.sample_rate / 2.0))

        onset_score = 0.0
        if self._prev_mag is not None and self._prev_mag.shape == mag.shape:
            positive_flux = np.maximum(mag - self._prev_mag, 0.0)
            denom = float(np.mean(self._prev_mag) + 1e-6)
            raw_flux = float(np.mean(positive_flux) / denom)
            self._onset_floor = 0.96 * self._onset_floor + 0.04 * raw_flux
            onset_score = max(0.0, raw_flux - self._onset_floor)
        self._prev_mag = mag
        energy_attack = max(0.0, (rms - self._rms_floor) / max(self._rms_floor, 0.015))
        bass_attack = max(0.0, (bass - self._bass_floor) / max(self._bass_floor, 0.001))
        onset_score += 0.25 * min(2.0, energy_attack) + 0.35 * min(2.0, bass_attack)
        self._rms_floor = self._smooth_floor(self._rms_floor, rms, rise=0.04, fall=0.18)
        self._bass_floor = self._smooth_floor(self._bass_floor, bass, rise=0.04, fall=0.18)

        return MusicFeatures(
            timestamp=timestamp,
            rms=rms,
            bass_energy=bass,
            mid_energy=mid,
            treble_energy=treble,
            onset_score=onset_score,
        )

    @staticmethod
    def _band_level(freqs: np.ndarray, mag: np.ndarray, low: float, high: float) -> float:
        mask = (freqs >= low) & (freqs < high)
        if not np.any(mask):
            return 0.0
        return float(np.sqrt(np.mean(np.square(mag[mask]))))

    @staticmethod
    def _smooth_floor(current: float, value: float, *, rise: float, fall: float) -> float:
        alpha = rise if value >= current else fall
        return (1.0 - alpha) * current + alpha * value


class BeatGate:
    """Select important beat/onset moments and drop tiny follow-up beats."""

    def __init__(
        self,
        *,
        beat_stride: int = 0,
        min_accent_interval_s: float = 0.45,
        accent_threshold: float = 0.16,
    ) -> None:
        self.beat_stride = int(beat_stride)
        self.min_accent_interval_s = max(0.0, float(min_accent_interval_s))
        self.accent_threshold = max(0.0, float(accent_threshold))
        self._beat_index = 0
        self._last_accent_at = -1e9
        self._onset_times: deque[float] = deque(maxlen=12)

    def consider(self, features: MusicFeatures) -> BeatDecision:
        bpm = self._estimate_bpm(features.timestamp)
        stride = self._resolve_stride(bpm)
        if features.is_silence:
            return BeatDecision(False, self._beat_index, stride, 0.0, bpm, "silence", features.timestamp)
        if features.onset_score < self.accent_threshold:
            return BeatDecision(
                False, self._beat_index, stride, features.onset_score, bpm, "weak_onset", features.timestamp
            )

        self._beat_index += 1
        self._onset_times.append(features.timestamp)
        bpm = self._estimate_bpm(features.timestamp)
        stride = self._resolve_stride(bpm)

        if features.timestamp - self._last_accent_at < self.min_accent_interval_s:
            return BeatDecision(
                False, self._beat_index, stride, features.onset_score, bpm, "too_close", features.timestamp
            )
        if stride > 1 and (self._beat_index - 1) % stride != 0:
            return BeatDecision(
                False, self._beat_index, stride, features.onset_score, bpm, "stride_skip", features.timestamp
            )

        self._last_accent_at = features.timestamp
        intensity = min(1.0, max(0.0, features.onset_score))
        return BeatDecision(True, self._beat_index, stride, intensity, bpm, "accent", features.timestamp)

    def _resolve_stride(self, bpm: float) -> int:
        if self.beat_stride > 0:
            return max(1, self.beat_stride)
        if bpm >= 150.0:
            return 4
        if bpm >= 115.0:
            return 2
        return 1

    def _estimate_bpm(self, fallback_now: float) -> float:
        del fallback_now
        if len(self._onset_times) < 3:
            return 0.0
        intervals = [
            b - a
            for a, b in zip(self._onset_times, list(self._onset_times)[1:])
            if 0.2 <= b - a <= 1.2
        ]
        if not intervals:
            return 0.0
        median = float(np.median(np.array(intervals, dtype=np.float32)))
        if median <= 0:
            return 0.0
        return 60.0 / median


class DancePhraseRenderer:
    """Render music features into short, visible, non-tiny motion phrases."""

    _STYLE = {
        "jazz": {"groove": 1.0, "pitch": 0.85, "accent": 0.95, "treble": 0.7, "period": 1.15},
        "electronic": {"groove": 1.18, "pitch": 1.0, "accent": 1.2, "treble": 0.9, "period": 0.86},
        "ambient": {"groove": 0.58, "pitch": 0.62, "accent": 0.42, "treble": 0.35, "period": 1.7},
    }

    def __init__(self, *, style: str = "jazz", fps: int = 50, min_motion_amplitude_deg: float = 2.5) -> None:
        self.style = style if style in self._STYLE else "jazz"
        self.fps = max(10, int(fps))
        self.min_motion_amplitude_deg = max(0.0, float(min_motion_amplitude_deg))
        self._yaw_phase = 0.0
        self._wrist_phase = 0.0
        self._pitch_phase = 0.0
        self._accent_side = 1.0

    def render(
        self,
        *,
        anchor: dict[str, float],
        features: list[MusicFeatures],
        beat: BeatDecision | None,
        duration_s: float = 0.8,
        amplitude_scale: float = 1.0,
    ) -> list[dict[str, float]]:
        if not features:
            return []

        recent = features[-8:]
        cfg = self._STYLE[self.style]
        level = min(1.0, max((f.rms for f in recent), default=0.0) * 5.0)
        bass_level = min(1.0, max((f.bass_energy for f in recent), default=0.0) * 32.0)
        mid_level = min(1.0, max((f.mid_energy for f in recent), default=0.0) * 18.0)
        treble_level = min(1.0, max((f.treble_energy for f in recent), default=0.0) * 44.0)
        if level < 0.04:
            return []

        scale = max(0.0, float(amplitude_scale))
        yaw_amp = (6.0 + 12.0 * bass_level) * cfg["groove"] * scale
        pitch_amp = (2.6 + 4.6 * level + 2.6 * mid_level) * cfg["pitch"] * scale
        wrist_roll_amp = (3.5 + 8.5 * treble_level) * cfg["treble"] * scale
        wrist_pitch_amp = (2.8 + 4.6 * treble_level + 2.2 * level) * cfg["treble"] * scale
        accent_pitch_amp = 0.0
        accent_elbow_amp = 0.0
        accent_yaw_amp = 0.0
        if beat is not None and beat.accent:
            accent_pitch_amp = (5.5 + 6.5 * beat.intensity) * cfg["accent"] * scale
            accent_elbow_amp = (2.5 + 3.0 * beat.intensity) * cfg["accent"] * scale
            accent_yaw_amp = self._accent_side * min(5.0, 2.0 + yaw_amp * 0.26)
            self._accent_side *= -1.0

        yaw_amp = self._visible(yaw_amp)
        pitch_amp = self._visible(pitch_amp)
        wrist_roll_amp = self._visible(wrist_roll_amp)
        wrist_pitch_amp = self._visible(wrist_pitch_amp)
        accent_pitch_amp = self._visible(accent_pitch_amp)
        accent_elbow_amp = self._visible(accent_elbow_amp)
        accent_yaw_amp = self._visible(accent_yaw_amp)
        if (
            yaw_amp == 0.0
            and pitch_amp == 0.0
            and wrist_roll_amp == 0.0
            and wrist_pitch_amp == 0.0
            and accent_pitch_amp == 0.0
            and accent_elbow_amp == 0.0
            and accent_yaw_amp == 0.0
        ):
            return []

        duration_s = max(0.2, float(duration_s))
        n_frames = max(1, round(duration_s * self.fps))
        period = float(cfg["period"])
        frames: list[dict[str, float]] = []
        accent_window = min(0.28, duration_s)
        for idx in range(n_frames):
            t = idx / self.fps
            pose: dict[str, float] = {}
            groove = math.sin(self._yaw_phase + 2.0 * math.pi * t / period)
            pitch_groove = 0.5 - 0.5 * math.cos(
                self._pitch_phase + 2.0 * math.pi * t / max(period * 0.58, 0.36)
            )
            accent_env = 0.0
            if accent_pitch_amp or accent_elbow_amp or accent_yaw_amp:
                accent_t = min(1.0, t / accent_window)
                accent_env = math.sin(math.pi * accent_t)
            if yaw_amp:
                pose["base_yaw"] = anchor.get("base_yaw", 0.0) + yaw_amp * groove + accent_yaw_amp * accent_env
            pitch_offset = -pitch_amp * pitch_groove * (0.45 + 0.55 * bass_level)
            if accent_pitch_amp:
                pitch_offset -= accent_pitch_amp * accent_env
            if abs(pitch_offset) >= self.min_motion_amplitude_deg:
                pose["base_pitch"] = anchor.get("base_pitch", 0.0) + pitch_offset
            if accent_elbow_amp:
                elbow_offset = accent_elbow_amp * accent_env
                if abs(elbow_offset) >= self.min_motion_amplitude_deg:
                    pose["elbow_pitch"] = anchor.get("elbow_pitch", 0.0) + elbow_offset
            if wrist_roll_amp:
                pose["wrist_roll"] = anchor.get("wrist_roll", 0.0) + wrist_roll_amp * math.sin(
                    self._wrist_phase + 2.0 * math.pi * t / max(period * 0.55, 0.3)
                )
            if wrist_pitch_amp:
                pose["wrist_pitch"] = anchor.get("wrist_pitch", 0.0) + wrist_pitch_amp * math.sin(
                    self._wrist_phase + math.pi / 3.0 + 2.0 * math.pi * t / max(period * 0.72, 0.42)
                )
            frames.append(pose)

        self._yaw_phase = (self._yaw_phase + 2.0 * math.pi * duration_s / period) % (2.0 * math.pi)
        self._pitch_phase = (
            self._pitch_phase + 2.0 * math.pi * duration_s / max(period * 0.58, 0.36)
        ) % (2.0 * math.pi)
        self._wrist_phase = (self._wrist_phase + 2.0 * math.pi * duration_s / max(period * 0.55, 0.3)) % (
            2.0 * math.pi
        )
        return frames

    def _visible(self, amplitude: float) -> float:
        return amplitude if abs(amplitude) >= self.min_motion_amplitude_deg else 0.0


class SounddeviceMusicSource:
    """Microphone or virtual-input fallback source using sounddevice."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 20,
        device: int | str | None = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = max(1, int(channels))
        self.chunk_samples = int(self.sample_rate * chunk_ms / 1000)
        self.device = device
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stream = None

    async def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioSourceError("sounddevice is not installed; cannot use source=mic") from exc

        def callback(indata, frames, time_info, status):
            del frames, time_info
            if status:
                return
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                pass

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self.chunk_samples,
            device=self.device,
            callback=callback,
        )
        self._stream.start()

    async def read_chunk(self) -> bytes | None:
        return await asyncio.to_thread(self._read_blocking)

    async def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _read_blocking(self) -> bytes | None:
        try:
            return self._queue.get(timeout=0.2)
        except queue.Empty:
            return None


class SyntheticMusicSource:
    """Deterministic beat source for no-hardware tests and demos."""

    def __init__(self, *, sample_rate: int = 16000, channels: int = 1, chunk_ms: int = 20, bpm: float = 120.0) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.chunk_ms = int(chunk_ms)
        self.bpm = float(bpm)
        self._cursor = 0
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def read_chunk(self) -> bytes | None:
        if not self._running:
            return None
        await asyncio.sleep(self.chunk_ms / 1000.0)
        n = max(1, int(self.sample_rate * self.chunk_ms / 1000))
        t = (np.arange(n, dtype=np.float32) + self._cursor) / self.sample_rate
        beat_period = 60.0 / max(self.bpm, 1.0)
        beat_phase = np.mod(t, beat_period)
        pulse = np.where(beat_phase < 0.055, np.sin(np.pi * beat_phase / 0.055), 0.0)
        signal = 0.22 * np.sin(2.0 * np.pi * 80.0 * t) + 0.65 * pulse + 0.04 * np.sin(2.0 * np.pi * 4200.0 * t)
        signal = np.clip(signal, -1.0, 1.0)
        pcm = (signal * 32767.0).astype("<i2")
        self._cursor += n
        return pcm.tobytes()

    async def stop(self) -> None:
        self._running = False


class MacSystemAudioSource:
    """macOS system-audio source backed by the Swift ScreenCaptureKit helper."""

    def __init__(self, *, chunk_ms: int = 20) -> None:
        self.sample_rate = 48000
        self.channels = 2
        self.chunk_bytes = int(self.sample_rate * self.channels * 2 * chunk_ms / 1000)
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr: list[str] = []
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        if platform.system() != "Darwin":
            raise AudioSourceError("source=system is only available on macOS; use source=mic or source=synthetic")
        cmd = self._helper_command()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AudioSourceError(
                "Swift toolchain not found; install Xcode Command Line Tools or use source=mic"
            ) from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def read_chunk(self) -> bytes | None:
        if self._proc is None or self._proc.stdout is None:
            return None
        try:
            data = await asyncio.wait_for(self._proc.stdout.readexactly(self.chunk_bytes), timeout=1.0)
            return data
        except asyncio.IncompleteReadError as exc:
            detail = self._stderr_detail()
            raise AudioSourceError(
                "macOS system audio capture stopped. Grant Screen Recording permission to LampgoAudioTap, "
                f"then restart lampgo. {detail}"
            ) from exc
        except TimeoutError:
            return None

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        task = self._stderr_task
        self._stderr_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _helper_command(self) -> list[str]:
        bin_path = os.environ.get("LAMPGO_AUDIO_TAP_BIN", "").strip()
        if bin_path:
            return [bin_path]
        package = Path(__file__).resolve().parents[1] / "macos" / "audio_capture"
        return ["swift", "run", "--package-path", str(package), "-c", "release", "LampgoAudioTap"]

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._stderr.append(text)
                del self._stderr[:-20]

    def _stderr_detail(self) -> str:
        if not self._stderr:
            return ""
        return "helper stderr: " + " | ".join(self._stderr[-4:])


def make_music_source(source: str) -> MusicAudioSource:
    normalized = str(source or "system").strip().lower()
    if normalized in {"system", "macos", "screencapturekit"}:
        return MacSystemAudioSource()
    if normalized in {"mic", "microphone", "blackhole"}:
        device = os.environ.get("LAMPGO_MUSIC_INPUT_DEVICE", "").strip() or None
        return SounddeviceMusicSource(device=device)
    if normalized in {"synthetic", "demo", "test"}:
        return SyntheticMusicSource()
    raise AudioSourceError(f"unknown music source: {source}")
