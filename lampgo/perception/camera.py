"""Camera helpers for attaching a live frame to LLM requests."""

from __future__ import annotations

import base64
import sys

import structlog

from lampgo.core.config import CameraConfig

logger = structlog.get_logger(__name__)


class CameraCapture:
    """Capture a JPEG frame and return it as a data URL."""

    WIDTH = 640
    HEIGHT = 480
    JPEG_QUALITY = 60

    def __init__(self, config: CameraConfig | None) -> None:
        self._config = config or CameraConfig()

    @property
    def enabled(self) -> bool:
        return bool(self._config.port.strip())

    @property
    def device_label(self) -> str:
        return self._config.port

    def capture_data_url(self) -> str | None:
        device = self._parse_device(self._config.port)
        if device is None:
            return None

        try:
            import cv2
        except ImportError:
            logger.warning("camera.capture_skipped", reason="opencv_not_installed")
            return None

        cap = None
        try:
            for backend in self._candidate_backends(cv2, device):
                cap = cv2.VideoCapture(device, backend) if backend is not None else cv2.VideoCapture(device)
                if cap.isOpened():
                    break
                cap.release()
                cap = None

            if cap is None or not cap.isOpened():
                logger.warning("camera.capture_failed", reason="cannot_open_device", port=self._config.port)
                return None

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)

            for _ in range(3):
                cap.read()

            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("camera.capture_failed", reason="empty_frame", port=self._config.port)
                return None

            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.JPEG_QUALITY])
            if not ok:
                logger.warning("camera.capture_failed", reason="jpeg_encode_failed")
                return None

            payload = base64.b64encode(encoded.tobytes()).decode("ascii")
            return f"data:image/jpeg;base64,{payload}"
        except Exception:
            logger.exception("camera.capture_failed", port=self._config.port)
            return None
        finally:
            if cap is not None:
                cap.release()

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
        backends: list[int | None] = [None]
        if isinstance(device, int) and sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
            backends.insert(0, cv2.CAP_AVFOUNDATION)
        elif isinstance(device, (int, str)) and sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
            backends.insert(0, cv2.CAP_V4L2)
        return backends
