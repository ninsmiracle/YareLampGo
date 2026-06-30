"""Local visual state estimation for the cat teaser skill.

The v1 algorithm intentionally stays fast and local: it tracks a bright marker
on the teaser wand, then estimates cat engagement from motion around that
marker. It does not try to classify cats with a neural model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np
import structlog

from lampgo.core.config import CameraConfig, DeviceEsp32Config

if TYPE_CHECKING:
    from lampgo.device import Esp32DeviceManager

logger = structlog.get_logger(__name__)

CatPlayState = Literal[
    "searching",
    "teasing",
    "engaged",
    "pounce",
    "caught",
    "rest",
    "unsafe_close",
]

_SUPPORTED_MARKER_COLORS = {"magenta", "green", "blue", "red", "yellow"}


class CatTeaserError(RuntimeError):
    """Base class for local cat teaser perception failures."""


class CatTeaserDependencyError(CatTeaserError):
    """Raised when optional perception dependencies are not installed."""


class CatTeaserCameraError(CatTeaserError):
    """Raised when no camera source is configured or readable."""


@dataclass(frozen=True)
class MarkerDetection:
    """Detected colored marker on the teaser wand."""

    x: float
    y: float
    radius: float
    area: float
    confidence: float
    frame_width: int
    frame_height: int

    @property
    def normalized_x(self) -> float:
        return self.x / max(float(self.frame_width), 1.0)

    @property
    def normalized_y(self) -> float:
        return self.y / max(float(self.frame_height), 1.0)


@dataclass(frozen=True)
class CatPlayObservation:
    """One perception tick consumed by the cat teaser motion policy."""

    state: CatPlayState
    marker: MarkerDetection | None
    motion_energy: float
    engagement_score: float
    motion_centroid: tuple[float, float] | None
    timestamp: float


def _import_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise CatTeaserDependencyError(
            "OpenCV is required for cat_teaser. Install the perception extra, "
            "for example: uv sync --extra perception"
        ) from exc
    return cv2


class CatTeaserFrameSource:
    """Read BGR frames from the lamp-head camera.

    ESP32 camera is preferred when enabled and online. If a local camera port is
    configured, it is used as a fallback or as the primary source.
    """

    WIDTH = 640
    HEIGHT = 480
    ESP32_HTTP_TIMEOUT_S = 1.2

    def __init__(
        self,
        camera_config: CameraConfig | None,
        *,
        device_esp32_config: DeviceEsp32Config | None = None,
        esp32_manager: Esp32DeviceManager | None = None,
    ) -> None:
        self._config = camera_config or CameraConfig()
        self._device_cfg = device_esp32_config
        self._esp32 = esp32_manager
        self._cv2 = None
        self._cap = None

    @property
    def enabled(self) -> bool:
        if self._config.port.strip():
            return True
        return bool(self._device_cfg and self._device_cfg.enabled)

    @property
    def device_label(self) -> str:
        if self._device_cfg is not None and self._device_cfg.enabled and self._esp32 is not None:
            host = self._esp32.get_active_host()
            if host:
                return f"esp32://{host}"
            if self._device_cfg.preferred_host:
                return f"esp32://{self._device_cfg.preferred_host} (offline)"
            return "esp32://(discovering)"
        return self._config.port

    def start(self) -> None:
        if not self.enabled:
            raise CatTeaserCameraError("cat_teaser needs a configured lamp-head camera")
        self._cv2 = _import_cv2()
        if self._config.port.strip():
            self._open_local_camera()

    def read(self):
        if self._cv2 is None:
            self.start()

        if self._device_cfg is not None and self._device_cfg.enabled and self._esp32 is not None:
            if self._esp32.is_online():
                frame = self._read_esp32_frame()
                if frame is not None:
                    self._esp32.mark_session_used()
                    return frame

        if self._cap is not None:
            ok, frame = self._cap.read()
            if ok and frame is not None:
                return frame
        return None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _open_local_camera(self) -> None:
        cv2 = self._cv2
        if cv2 is None:
            return
        device = self._parse_device(self._config.port)
        if device is None:
            return
        for backend in self._candidate_backends(cv2, device):
            cap = cv2.VideoCapture(device, backend) if backend is not None else cv2.VideoCapture(device)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)
                self._cap = cap
                return
            cap.release()
        logger.warning("cat_teaser.local_camera_unavailable", port=self._config.port)

    def _read_esp32_frame(self):
        cv2 = self._cv2
        if cv2 is None or self._esp32 is None:
            return None
        base_url = self._esp32.get_active_base_url()
        if not base_url:
            return None
        try:
            import httpx
        except ImportError:
            logger.warning("cat_teaser.httpx_missing")
            return None
        try:
            with httpx.Client(timeout=self.ESP32_HTTP_TIMEOUT_S, trust_env=False) as client:
                resp = client.get(f"{base_url}/capture")
            if resp.status_code != 200:
                logger.warning("cat_teaser.esp32_http_error", status=resp.status_code)
                return None
            data = np.frombuffer(resp.content, dtype=np.uint8)
            return cv2.imdecode(data, cv2.IMREAD_COLOR)
        except Exception:
            logger.debug("cat_teaser.esp32_capture_failed", exc_info=True)
            return None

    @staticmethod
    def _parse_device(raw: str) -> int | str | None:
        value = raw.strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        return value

    @staticmethod
    def _candidate_backends(cv2, device: int | str) -> list[int | None]:
        import sys

        backends: list[int | None] = [None]
        if isinstance(device, int) and sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
            backends.insert(0, cv2.CAP_AVFOUNDATION)
        elif isinstance(device, (int, str)) and sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
            backends.insert(0, cv2.CAP_V4L2)
        return backends


class CatToyTracker:
    """HSV marker tracker for the teaser wand tip."""

    def __init__(self, *, marker_color: str = "magenta", min_area: float = 40.0) -> None:
        color = marker_color.strip().lower()
        if color not in _SUPPORTED_MARKER_COLORS:
            supported = ", ".join(sorted(_SUPPORTED_MARKER_COLORS))
            raise ValueError(f"Unsupported marker_color={marker_color!r}; expected one of: {supported}")
        self.marker_color = color
        self.min_area = float(min_area)

    def track(self, frame) -> MarkerDetection | None:
        cv2 = _import_cv2()
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = self._mask_for_color(cv2, hsv)
        kernel = np.ones((5, 5), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(contour))
        if area < self.min_area:
            return None
        (x, y), radius = cv2.minEnclosingCircle(contour)
        confidence_denominator = max(self.min_area * 8.0, float(width * height) * 0.002)
        confidence = min(1.0, area / confidence_denominator)
        return MarkerDetection(
            x=float(x),
            y=float(y),
            radius=float(radius),
            area=area,
            confidence=confidence,
            frame_width=int(width),
            frame_height=int(height),
        )

    def _mask_for_color(self, cv2, hsv):
        ranges = {
            "magenta": [((140, 70, 70), (175, 255, 255))],
            "green": [((40, 55, 55), (85, 255, 255))],
            "blue": [((95, 65, 55), (130, 255, 255))],
            "yellow": [((20, 70, 80), (38, 255, 255))],
            "red": [((0, 70, 70), (10, 255, 255)), ((170, 70, 70), (179, 255, 255))],
        }[self.marker_color]
        mask = None
        for lower, upper in ranges:
            part = cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        return mask


@dataclass
class CatPlayStateEstimator:
    """Estimate play state from marker tracking and local motion energy."""

    motion_threshold: int = 18
    marker_timeout_s: float = 0.8
    rest_after_s: float = 2.0
    unsafe_radius_ratio: float = 0.22
    unsafe_area_ratio: float = 0.055
    _previous_gray: object | None = field(default=None, init=False, repr=False)
    _last_marker: MarkerDetection | None = field(default=None, init=False)
    _last_seen_at: float | None = field(default=None, init=False)
    _engagement_score: float = field(default=0.0, init=False)

    def update(
        self,
        frame,
        marker: MarkerDetection | None,
        *,
        timestamp: float | None = None,
    ) -> CatPlayObservation:
        cv2 = _import_cv2()
        now = time.monotonic() if timestamp is None else float(timestamp)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        motion_mask = self._motion_mask(cv2, gray)

        anchor = marker or self._last_marker
        motion_energy, centroid = self._local_motion(motion_mask, anchor)
        state = self._classify(marker, motion_energy, now)
        self._update_engagement(marker, motion_energy, state)

        if marker is not None:
            self._last_marker = marker
            self._last_seen_at = now
        self._previous_gray = gray

        return CatPlayObservation(
            state=state,
            marker=marker,
            motion_energy=motion_energy,
            engagement_score=self._engagement_score,
            motion_centroid=centroid,
            timestamp=now,
        )

    def _motion_mask(self, cv2, gray):
        if self._previous_gray is None:
            return np.zeros_like(gray, dtype=np.uint8)
        delta = cv2.absdiff(self._previous_gray, gray)
        _ret, mask = cv2.threshold(delta, self.motion_threshold, 255, cv2.THRESH_BINARY)
        return mask

    def _local_motion(
        self,
        motion_mask,
        anchor: MarkerDetection | None,
    ) -> tuple[float, tuple[float, float] | None]:
        height, width = motion_mask.shape[:2]
        if anchor is None:
            nonzero = int(np.count_nonzero(motion_mask))
            return nonzero / max(float(width * height), 1.0), None

        radius = max(42, int(anchor.radius * 4.0))
        x0 = max(0, int(anchor.x) - radius)
        x1 = min(width, int(anchor.x) + radius)
        y0 = max(0, int(anchor.y) - radius)
        y1 = min(height, int(anchor.y) + radius)
        if x1 <= x0 or y1 <= y0:
            return 0.0, None

        roi = motion_mask[y0:y1, x0:x1]
        energy = float(np.count_nonzero(roi)) / max(float(roi.size), 1.0)
        ys, xs = np.nonzero(roi)
        if len(xs) == 0:
            return energy, None
        cx = (x0 + float(np.mean(xs))) / max(float(width), 1.0)
        cy = (y0 + float(np.mean(ys))) / max(float(height), 1.0)
        return energy, (cx, cy)

    def _classify(
        self,
        marker: MarkerDetection | None,
        motion_energy: float,
        now: float,
    ) -> CatPlayState:
        if marker is not None and self._is_unsafe_close(marker):
            return "unsafe_close"

        if marker is None:
            recent_marker = self._last_seen_at is not None and now - self._last_seen_at <= self.marker_timeout_s
            if recent_marker and motion_energy > 0.035:
                return "caught"
            if self._last_seen_at is not None and now - self._last_seen_at > self.rest_after_s:
                if self._engagement_score < 0.12 and motion_energy < 0.02:
                    return "rest"
            return "searching"

        if motion_energy > 0.16:
            return "pounce"
        if motion_energy > 0.045 or self._engagement_score > 0.35:
            return "engaged"
        return "teasing"

    def _is_unsafe_close(self, marker: MarkerDetection) -> bool:
        min_dim = max(float(min(marker.frame_width, marker.frame_height)), 1.0)
        frame_area = max(float(marker.frame_width * marker.frame_height), 1.0)
        return marker.radius / min_dim > self.unsafe_radius_ratio or marker.area / frame_area > self.unsafe_area_ratio

    def _update_engagement(
        self,
        marker: MarkerDetection | None,
        motion_energy: float,
        state: CatPlayState,
    ) -> None:
        marker_score = marker.confidence * 0.25 if marker is not None else 0.0
        burst_bonus = 0.25 if state in {"pounce", "caught"} else 0.0
        if state in {"searching", "rest", "unsafe_close"}:
            raw = 0.0
        else:
            raw = min(1.0, marker_score + motion_energy * 6.0 + burst_bonus)
        self._engagement_score = self._engagement_score * 0.78 + raw * 0.22
