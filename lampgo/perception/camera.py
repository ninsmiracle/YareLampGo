"""Camera helpers for attaching a live frame to LLM requests.

Capture source routing (see also ``lampgo.device.esp32``):

    configured source          | cold start, ESP32 offline | mid-session, ESP32 drops
    ---------------------------|---------------------------|--------------------------
    ESP32 + local (enabled=T)  | silent fallback to cv2    | return None (LLM: "I can't see")
    ESP32 only (enabled=T, no port) | silent None (no local) | return None
    local only (enabled=F)     | cv2 directly              | n/a

The "mid-session drop" behavior is deliberate: flipping camera source during a
live conversation confuses both the user and the LLM ("why does my face look
different now?"). Instead we let the LLM observe a missing frame and describe
the situation naturally.
"""

from __future__ import annotations

import base64
import sys
from typing import TYPE_CHECKING

import structlog

from lampgo.core.config import CameraConfig, DeviceEsp32Config

if TYPE_CHECKING:
    from lampgo.device import Esp32DeviceManager

logger = structlog.get_logger(__name__)


class CameraCapture:
    """Capture a JPEG frame from either an ESP32 camera or a local cv2 device."""

    WIDTH = 640
    HEIGHT = 480
    JPEG_QUALITY = 60
    ESP32_HTTP_TIMEOUT_S = 8.0

    def __init__(
        self,
        config: CameraConfig | None,
        *,
        device_esp32_config: DeviceEsp32Config | None = None,
        esp32_manager: Esp32DeviceManager | None = None,
    ) -> None:
        self._config = config or CameraConfig()
        self._device_cfg = device_esp32_config
        self._esp32 = esp32_manager

    @property
    def enabled(self) -> bool:
        if self._config.port.strip():
            return True
        if self._device_cfg is not None and self._device_cfg.enabled:
            return True
        return False

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

    def capture_data_url(self) -> str | None:
        """Return ``data:image/jpeg;base64,...`` for the next frame, or None.

        Routing is computed on every call so a mid-session switch to ESP32
        takes effect on the next agent tool call without rebuilding LLMClient.
        """
        use_esp32 = bool(self._device_cfg and self._device_cfg.enabled and self._esp32 is not None)

        if use_esp32 and self._esp32.is_online():
            url = self._esp32.get_active_base_url()
            if url is not None:
                data = self._capture_via_esp32(url)
                if data is not None:
                    self._esp32.mark_session_used()
                    return data
                logger.warning("camera.esp32_capture_failed", url=url)
                if self._esp32.session_used():
                    return None

        if use_esp32 and self._esp32.session_used():
            logger.info("camera.esp32_offline_midsession", reason="returning_none_so_llm_sees_gap")
            return None

        if not self._config.port.strip():
            return None
        return self._capture_via_local_cv2()

    def _capture_via_esp32(self, base_url: str) -> str | None:
        try:
            import httpx
        except ImportError:
            logger.warning("camera.esp32_httpx_missing")
            return None
        try:
            with httpx.Client(timeout=self.ESP32_HTTP_TIMEOUT_S, trust_env=False) as client:
                resp = client.get(f"{base_url}/capture")
            if resp.status_code != 200:
                logger.warning("camera.esp32_http_error", status=resp.status_code)
                return None
            payload = base64.b64encode(resp.content).decode("ascii")
            return f"data:image/jpeg;base64,{payload}"
        except httpx.TimeoutException as exc:
            logger.warning(
                "camera.esp32_capture_timeout",
                url=base_url,
                timeout_s=self.ESP32_HTTP_TIMEOUT_S,
                error_type=type(exc).__name__,
            )
            return None
        except httpx.RequestError as exc:
            logger.warning(
                "camera.esp32_capture_request_failed",
                url=base_url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None
        except Exception:
            logger.exception("camera.esp32_capture_exception", url=base_url)
            return None

    def _capture_via_local_cv2(self) -> str | None:
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
