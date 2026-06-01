from __future__ import annotations

from types import SimpleNamespace

import httpx

from lampgo.core.config import CameraConfig
from lampgo.perception.camera import CameraCapture


def test_http_snapshot_camera_source_returns_data_url(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is True

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]):
            calls.append((url, headers))
            return SimpleNamespace(status_code=200, content=b"jpeg-bytes", headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(httpx, "Client", FakeClient)

    camera = CameraCapture(CameraConfig(port="http://127.0.0.1:18765/snapshot.jpg"))

    assert camera.capture_data_url() == "data:image/jpeg;base64,anBlZy1ieXRlcw=="
    assert calls == [("http://127.0.0.1:18765/snapshot.jpg", {"Accept": "image/jpeg,image/png,*/*"})]


def test_http_snapshot_camera_source_defaults_octet_stream_to_jpeg(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]):
            return SimpleNamespace(
                status_code=200,
                content=b"raw",
                headers={"content-type": "application/octet-stream"},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    camera = CameraCapture(CameraConfig(port="http://127.0.0.1:18765/snapshot.jpg"))

    assert camera.capture_data_url() == "data:image/jpeg;base64,cmF3"


def test_mjpeg_url_stays_available_for_cv2_stream_handling() -> None:
    assert not CameraCapture._looks_like_http_snapshot_url("http://127.0.0.1:18765/mjpeg")
    assert not CameraCapture._looks_like_http_snapshot_url("http://127.0.0.1:18765/video.mjpg")
    assert CameraCapture._looks_like_http_snapshot_url("http://127.0.0.1:18765/snapshot.jpg")
