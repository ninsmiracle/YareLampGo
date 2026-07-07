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
_LOCAL_VIDEO_DEVICE_PREFIX = "/dev/video"


class CatTeaserError(RuntimeError):
    """Base class for local cat teaser perception failures."""


class CatTeaserDependencyError(CatTeaserError):
    """Raised when optional perception dependencies are not installed."""


class CatTeaserCameraError(CatTeaserError):
    """Raised when no camera source is configured or readable."""


def is_supported_local_camera_port(raw: str) -> bool:
    """Return True for local camera selectors that cannot become URL/file reads."""
    value = raw.strip()
    if not value:
        return True
    if value.isdigit():
        return True
    if value.startswith(_LOCAL_VIDEO_DEVICE_PREFIX):
        suffix = value.removeprefix(_LOCAL_VIDEO_DEVICE_PREFIX)
        return bool(suffix) and suffix.isdigit()
    return False


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
    contact_motion_energy: float = 0.0
    marker_disturbance: float = 0.0


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
    configured, it is used as a fallback or as the primary source. In no-hardware
    mode callers may allow an implicit local camera fallback, which tries device
    index 0 without persisting it to config.
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
        allow_local_camera_fallback: bool = False,
        fallback_local_port: str = "0",
    ) -> None:
        self._config = camera_config or CameraConfig()
        self._device_cfg = device_esp32_config
        self._esp32 = esp32_manager
        self._allow_local_camera_fallback = allow_local_camera_fallback
        self._fallback_local_port = str(fallback_local_port or "0")
        self._cv2 = None
        self._cap = None
        self._local_label = ""

    @property
    def enabled(self) -> bool:
        if self._local_camera_port():
            return True
        return bool(self._device_cfg and self._device_cfg.enabled)

    @property
    def device_label(self) -> str:
        if self._cap is not None and self._local_label:
            return self._local_label
        if self._device_cfg is not None and self._device_cfg.enabled and self._esp32 is not None:
            host = self._esp32.get_active_host()
            if host:
                return f"esp32://{host}"
            if self._device_cfg.preferred_host:
                return f"esp32://{self._device_cfg.preferred_host} (offline)"
            return "esp32://(discovering)"
        port = self._local_camera_port()
        if port:
            suffix = " (fallback)" if self._using_implicit_local_fallback() else ""
            return f"local://{port}{suffix}"
        return ""

    def start(self) -> None:
        if not self.enabled:
            raise CatTeaserCameraError("cat_teaser needs a configured lamp-head camera")
        self._cv2 = _import_cv2()
        if self._local_camera_port():
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
        port = self._local_camera_port()
        device = self._parse_device(port)
        if device is None:
            return
        for backend in self._candidate_backends(cv2, device):
            cap = cv2.VideoCapture(device, backend) if backend is not None else cv2.VideoCapture(device)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)
                self._cap = cap
                suffix = " (fallback)" if self._using_implicit_local_fallback() else ""
                self._local_label = f"local://{port}{suffix}"
                return
            cap.release()
        logger.warning(
            "cat_teaser.local_camera_unavailable",
            port=port,
            fallback=self._using_implicit_local_fallback(),
        )

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
        if value.startswith(_LOCAL_VIDEO_DEVICE_PREFIX):
            suffix = value.removeprefix(_LOCAL_VIDEO_DEVICE_PREFIX)
            if suffix.isdigit():
                return value
        logger.warning("cat_teaser.local_camera_port_rejected", port=value)
        return None

    def _local_camera_port(self) -> str:
        configured = self._config.port.strip()
        if configured:
            return configured if is_supported_local_camera_port(configured) else ""
        if self._allow_local_camera_fallback:
            return self._fallback_local_port.strip() or "0"
        return ""

    def _using_implicit_local_fallback(self) -> bool:
        return not self._config.port.strip() and bool(self._allow_local_camera_fallback)

    @staticmethod
    def _candidate_backends(cv2, device: int | str) -> list[int | None]:
        import sys

        backends: list[int | None] = [None]
        if isinstance(device, int) and sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
            backends.insert(0, cv2.CAP_AVFOUNDATION)
        elif isinstance(device, (int, str)) and sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
            backends.insert(0, cv2.CAP_V4L2)
        return backends


class CatTeaserDebugView:
    """OpenCV debug window for cat teaser perception."""

    def __init__(self, *, enabled: bool = True, window_name: str = "LampGo Cat Teaser Vision") -> None:
        self.enabled = enabled
        self.window_name = window_name
        self._cv2 = None
        self._window_open = False
        self._warned = False

    def render(
        self,
        frame,
        observation: CatPlayObservation,
        *,
        elapsed_s: float,
        marker_color: str,
        event_text: str | None = None,
    ) -> bool:
        """Show an annotated frame.

        Returns True when the user asks to stop the skill by pressing q/esc or
        closing the preview window.
        """
        if not self.enabled:
            return False
        try:
            cv2 = self._ensure_cv2()
            self._ensure_window(cv2, frame)
            display = self._annotate_frame(cv2, frame, observation, elapsed_s, marker_color, event_text)
            cv2.imshow(self.window_name, display)
            key = cv2.waitKey(1) & 0xFF
            if key in {27, ord("q")}:
                return True
            return self._window_was_closed(cv2)
        except Exception as exc:
            self._disable_after_error(exc)
            return False

    def close(self) -> None:
        if self._cv2 is None or not self._window_open:
            return
        try:
            self._cv2.destroyWindow(self.window_name)
            self._cv2.waitKey(1)
        except Exception:
            logger.debug("cat_teaser.debug_view_close_failed", exc_info=True)
        finally:
            self._window_open = False

    def _ensure_cv2(self):
        if self._cv2 is None:
            self._cv2 = _import_cv2()
        return self._cv2

    def _ensure_window(self, cv2, frame) -> None:
        if self._window_open:
            return
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        height, width = frame.shape[:2]
        cv2.resizeWindow(self.window_name, min(width, 960), min(height, 720))
        self._window_open = True

    def _annotate_frame(
        self,
        cv2,
        frame,
        observation: CatPlayObservation,
        elapsed_s: float,
        marker_color: str,
        event_text: str | None,
    ):
        display = frame.copy()
        marker = observation.marker
        if marker is not None:
            color = self._bgr_for_marker(marker_color)
            center = (int(marker.x), int(marker.y))
            radius = max(3, int(marker.radius))
            cv2.circle(display, center, radius, color, 2)
            cv2.circle(display, center, 3, color, -1)
            self._put_text(cv2, display, f"{marker_color} marker {marker.confidence:.2f}", center[0] + 8, center[1] - 8)
        else:
            self._put_text(cv2, display, f"{marker_color} marker lost", 14, 76, color=(0, 0, 255))

        if observation.motion_centroid is not None:
            height, width = display.shape[:2]
            cx = int(observation.motion_centroid[0] * width)
            cy = int(observation.motion_centroid[1] * height)
            cv2.drawMarker(display, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 18, 2)

        lines = [
            f"t={elapsed_s:.1f}s state={observation.state}",
            f"motion={observation.motion_energy:.3f} engagement={observation.engagement_score:.2f}",
            f"contact={observation.contact_motion_energy:.3f} disturb={observation.marker_disturbance:.3f}",
            "q/esc: stop cat_teaser",
        ]
        if event_text:
            lines.insert(2, f"event: {event_text}")
        self._draw_panel(cv2, display, lines)
        return display

    @staticmethod
    def _draw_panel(cv2, display, lines: list[str]) -> None:
        x, y = 12, 18
        line_h = 24
        width = max(300, max(len(line) for line in lines) * 10)
        height = line_h * len(lines) + 14
        overlay = display.copy()
        cv2.rectangle(overlay, (8, 8), (8 + width, 8 + height), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.48, display, 0.52, 0, display)
        for idx, line in enumerate(lines):
            CatTeaserDebugView._put_text(cv2, display, line, x, y + idx * line_h)

    @staticmethod
    def _put_text(cv2, display, text: str, x: int, y: int, *, color: tuple[int, int, int] = (255, 255, 255)) -> None:
        cv2.putText(display, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(display, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 1, cv2.LINE_AA)

    @staticmethod
    def _bgr_for_marker(marker_color: str) -> tuple[int, int, int]:
        return {
            "magenta": (255, 0, 255),
            "green": (0, 255, 0),
            "blue": (255, 0, 0),
            "red": (0, 0, 255),
            "yellow": (0, 255, 255),
        }.get(marker_color, (255, 255, 255))

    def _window_was_closed(self, cv2) -> bool:
        if not self._window_open:
            return False
        try:
            return cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1
        except Exception:
            return False

    def _disable_after_error(self, exc: Exception) -> None:
        self.enabled = False
        if self._warned:
            return
        self._warned = True
        logger.warning(
            "cat_teaser.debug_view_unavailable",
            error=str(exc),
            hint="OpenCV GUI preview is unavailable; cat_teaser continues without the popup window.",
        )


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
    min_caught_lost_s: float = 0.28
    min_caught_lost_frames: int = 3
    stable_marker_frames: int = 3
    caught_motion_threshold: float = 0.22
    pounce_motion_threshold: float = 0.19
    visible_caught_motion_threshold: float = 0.18
    visible_caught_contact_threshold: float = 0.27
    visible_caught_disturbed_contact_threshold: float = 0.18
    visible_caught_marker_disturbance: float = 0.055
    visible_caught_strong_marker_disturbance: float = 0.09
    rest_after_s: float = 2.0
    unsafe_radius_ratio: float = 0.22
    unsafe_area_ratio: float = 0.055
    _previous_gray: object | None = field(default=None, init=False, repr=False)
    _last_marker: MarkerDetection | None = field(default=None, init=False)
    _last_seen_at: float | None = field(default=None, init=False)
    _lost_started_at: float | None = field(default=None, init=False)
    _marker_seen_frames: int = field(default=0, init=False)
    _marker_lost_frames: int = field(default=0, init=False)
    _seen_frames_before_loss: int = field(default=0, init=False)
    _pounce_motion_frames: int = field(default=0, init=False)
    _last_pounce_at: float | None = field(default=None, init=False)
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
        motion_mask = self._without_marker_self_motion(cv2, motion_mask, marker)

        anchor = marker or self._last_marker
        motion_energy, centroid = self._local_motion(motion_mask, anchor)
        contact_motion_energy = self._contact_motion(motion_mask, marker)
        marker_disturbance = self._marker_disturbance(marker) if marker is not None else 0.0
        self._update_marker_history(marker, now)
        state = self._classify(marker, motion_energy, contact_motion_energy, marker_disturbance, now)
        self._update_pounce_history(state, now)
        self._update_engagement(marker, motion_energy, state)

        if marker is not None:
            self._last_marker = marker
        self._previous_gray = gray

        return CatPlayObservation(
            state=state,
            marker=marker,
            motion_energy=motion_energy,
            engagement_score=self._engagement_score,
            motion_centroid=centroid,
            timestamp=now,
            contact_motion_energy=contact_motion_energy,
            marker_disturbance=marker_disturbance,
        )

    def _motion_mask(self, cv2, gray):
        if self._previous_gray is None:
            return np.zeros_like(gray, dtype=np.uint8)
        delta = cv2.absdiff(self._previous_gray, gray)
        _ret, mask = cv2.threshold(delta, self.motion_threshold, 255, cv2.THRESH_BINARY)
        return mask

    def _without_marker_self_motion(
        self,
        cv2,
        motion_mask,
        marker: MarkerDetection | None,
    ):
        if marker is None and self._last_marker is None:
            return motion_mask
        mask = motion_mask.copy()
        for detection in (marker, self._last_marker):
            if detection is None:
                continue
            center = (int(detection.x), int(detection.y))
            radius = max(20, int(detection.radius * 1.8))
            cv2.circle(mask, center, radius, 0, -1)
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

    def _contact_motion(
        self,
        motion_mask,
        marker: MarkerDetection | None,
    ) -> float:
        if marker is None:
            return 0.0
        height, width = motion_mask.shape[:2]
        cx = int(marker.x)
        cy = int(marker.y)
        inner = max(20, int(marker.radius * 1.7))
        outer = max(inner + 12, int(marker.radius * 3.3))
        x0 = max(0, cx - outer)
        x1 = min(width, cx + outer)
        y0 = max(0, cy - outer)
        y1 = min(height, cy + outer)
        if x1 <= x0 or y1 <= y0:
            return 0.0

        roi = motion_mask[y0:y1, x0:x1]
        yy, xx = np.ogrid[y0:y1, x0:x1]
        distance_sq = (xx - marker.x) ** 2 + (yy - marker.y) ** 2
        ring = (distance_sq >= inner**2) & (distance_sq <= outer**2)
        ring_area = int(np.count_nonzero(ring))
        if ring_area <= 0:
            return 0.0
        return float(np.count_nonzero(roi[ring])) / float(ring_area)

    def _classify(
        self,
        marker: MarkerDetection | None,
        motion_energy: float,
        contact_motion_energy: float,
        marker_disturbance: float,
        now: float,
    ) -> CatPlayState:
        if marker is not None and self._is_unsafe_close(marker):
            return "unsafe_close"

        if marker is None:
            recent_marker = self._last_seen_at is not None and now - self._last_seen_at <= self.marker_timeout_s
            lost_duration = now - self._lost_started_at if self._lost_started_at is not None else 0.0
            sustained_loss = (
                self._marker_lost_frames >= self.min_caught_lost_frames
                and lost_duration >= self.min_caught_lost_s
            )
            stable_before_loss = self._seen_frames_before_loss >= self.stable_marker_frames
            was_pouncing = self._last_pounce_at is not None and now - self._last_pounce_at <= self.marker_timeout_s
            if (
                recent_marker
                and sustained_loss
                and stable_before_loss
                and was_pouncing
                and motion_energy > self.caught_motion_threshold
            ):
                return "caught"
            if self._last_seen_at is not None and now - self._last_seen_at > self.rest_after_s:
                if self._engagement_score < 0.12 and motion_energy < 0.02:
                    return "rest"
            return "searching"

        if self._is_visible_contact(marker, motion_energy, contact_motion_energy, marker_disturbance):
            return "caught"

        if motion_energy > self.pounce_motion_threshold:
            self._pounce_motion_frames += 1
        else:
            self._pounce_motion_frames = 0
        if self._pounce_motion_frames >= 2 or motion_energy > self.pounce_motion_threshold * 2.2:
            return "pounce"
        if motion_energy > 0.045 or self._engagement_score > 0.35:
            return "engaged"
        return "teasing"

    def _is_visible_contact(
        self,
        marker: MarkerDetection,
        motion_energy: float,
        contact_motion_energy: float,
        marker_disturbance: float,
    ) -> bool:
        del marker
        if contact_motion_energy < self.visible_caught_disturbed_contact_threshold:
            return False

        strong_contact = (
            motion_energy >= self.visible_caught_motion_threshold * 1.8
            and contact_motion_energy >= self.visible_caught_contact_threshold
            and marker_disturbance >= self.visible_caught_marker_disturbance
        )
        disturbed_contact = (
            motion_energy >= self.visible_caught_motion_threshold
            and contact_motion_energy >= self.visible_caught_contact_threshold
            and marker_disturbance >= self.visible_caught_strong_marker_disturbance
        )
        near_tip_nudge = (
            motion_energy >= self.visible_caught_motion_threshold
            and contact_motion_energy >= self.visible_caught_contact_threshold * 1.1
            and marker_disturbance >= self.visible_caught_marker_disturbance
        )
        if not (strong_contact or disturbed_contact or near_tip_nudge):
            return False
        return True

    def _marker_disturbance(self, marker: MarkerDetection) -> float:
        previous = self._last_marker
        if previous is None:
            return 0.0
        dx = marker.normalized_x - previous.normalized_x
        dy = marker.normalized_y - previous.normalized_y
        shift = (dx * dx + dy * dy) ** 0.5 * 4.0
        area_change = abs(marker.area - previous.area) / max(marker.area, previous.area, 1.0)
        radius_change = abs(marker.radius - previous.radius) / max(marker.radius, previous.radius, 1.0)
        confidence_drop = max(0.0, previous.confidence - marker.confidence)
        return max(shift, area_change, radius_change * 1.4, confidence_drop)

    def _update_marker_history(self, marker: MarkerDetection | None, now: float) -> None:
        if marker is not None:
            self._marker_seen_frames += 1
            self._marker_lost_frames = 0
            self._lost_started_at = None
            self._seen_frames_before_loss = 0
            self._last_seen_at = now
            return

        if self._marker_lost_frames == 0:
            self._lost_started_at = now
            self._seen_frames_before_loss = self._marker_seen_frames
        self._marker_lost_frames += 1
        self._marker_seen_frames = 0
        self._pounce_motion_frames = 0

    def _update_pounce_history(self, state: CatPlayState, now: float) -> None:
        if state == "pounce":
            self._last_pounce_at = now

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
