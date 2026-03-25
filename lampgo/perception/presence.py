"""Lightweight person presence detection using OpenCV.

Runs in a background asyncio task, publishes PresenceDetected events
to the event bus when a person enters or leaves the frame.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog

from lampgo.core.events import Event, EventBus

logger = structlog.get_logger(__name__)


@dataclass
class PresenceDetected(Event):
    detected: bool
    confidence: float = 0.0
    timestamp: float = 0.0


class PresenceDetector:
    """OpenCV-based face/person detector running in a polling loop."""

    def __init__(self, events: EventBus, camera_index: int = 0, interval: float = 1.0) -> None:
        self._events = events
        self._camera_index = camera_index
        self._interval = interval
        self._running = False
        self._last_state = False
        self._cap = None
        self._cascade = None

    async def start(self) -> None:
        try:
            import cv2

            self._cap = cv2.VideoCapture(self._camera_index)
            if not self._cap.isOpened():
                logger.warning("presence.camera_unavailable", index=self._camera_index)
                return

            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._cascade = cv2.CascadeClassifier(cascade_path)
            self._running = True
            logger.info("presence.started", camera=self._camera_index)
            asyncio.get_event_loop().create_task(self._loop())
        except ImportError:
            logger.warning("presence.opencv_not_installed")

    async def stop(self) -> None:
        self._running = False
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    async def _loop(self) -> None:
        import cv2

        while self._running:
            try:
                ret, frame = self._cap.read()
                if not ret:
                    await asyncio.sleep(self._interval)
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
                detected = len(faces) > 0
                confidence = float(len(faces)) / 5.0 if detected else 0.0

                if detected != self._last_state:
                    self._last_state = detected
                    await self._events.publish(
                        PresenceDetected(
                            detected=detected,
                            confidence=min(1.0, confidence),
                            timestamp=time.monotonic(),
                        )
                    )
                    logger.info("presence.changed", detected=detected)

            except Exception:
                logger.exception("presence.detection_error")

            await asyncio.sleep(self._interval)
