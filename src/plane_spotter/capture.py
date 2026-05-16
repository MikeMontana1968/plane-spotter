"""Threaded frame capture with a pre-roll ring buffer.

Reading from cv2.VideoCapture in the main loop blocks; running it in its own
thread lets the detector consume "latest" frames without backing up. The ring
buffer holds the last N seconds of frames so we can include them in an
incident folder retroactively.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import cv2

logger = logging.getLogger(__name__)


def _enumerate_dshow_devices() -> list[str]:
    """Return DirectShow input device names in index order.

    Raises RuntimeError if pygrabber is unavailable or enumeration fails —
    we can't honor a name-based camera selection without it.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError as e:
        raise RuntimeError(
            "pygrabber is required for name-based camera selection. "
            "Run 'uv sync'."
        ) from e
    try:
        return list(FilterGraph().get_input_devices())
    except Exception as e:
        raise RuntimeError(f"DirectShow camera enumeration failed: {e}") from e


def _resolve_camera_index(name: str) -> tuple[int, str]:
    """Find the first device whose friendly name contains `name` (case-insensitive).

    Returns (index, full_device_name). Raises RuntimeError listing the
    available devices if no match is found.
    """
    devices = _enumerate_dshow_devices()
    needle = name.lower()
    for i, dev in enumerate(devices):
        if needle in dev.lower():
            return i, dev
    listing = "\n  ".join(f"[{i}] {d}" for i, d in enumerate(devices)) or "(none)"
    raise RuntimeError(
        f"No camera matched name '{name}'. Available devices:\n  {listing}"
    )


class FrameSource:
    def __init__(self, config):
        self.config = config
        self.cap: Optional[cv2.VideoCapture] = None
        maxlen = max(1, int(config.preroll_seconds * config.target_fps))
        self.buffer: deque = deque(maxlen=maxlen)
        self.latest: Optional[tuple[float, "cv2.Mat"]] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        index, device_name = _resolve_camera_index(self.config.camera_name)
        logger.info("camera resolved: '%s' -> [%d] %s",
                    self.config.camera_name, index, device_name)

        if self.config.use_dshow_backend:
            self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        else:
            self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Could not open camera [{index}] {device_name!r}. "
                f"Try toggling use_dshow_backend in config."
            )

        name_lower = device_name.lower()
        if any(s.lower() in name_lower for s in self.config.force_mjpg_camera_names):
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            self.cap.set(cv2.CAP_PROP_FOURCC, fourcc)
            logger.info("forcing MJPG fourcc for %s", device_name)

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.capture_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.capture_height)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.target_fps)

        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="FrameSource"
        )
        self._thread.start()

    def _loop(self) -> None:
        consecutive_failures = 0
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    logger.warning("camera read failing repeatedly")
                    consecutive_failures = 0
                time.sleep(0.05)
                continue
            consecutive_failures = 0
            ts = time.time()
            with self._lock:
                self.latest = (ts, frame)
                self.buffer.append((ts, frame.copy()))

    def get_latest(self) -> Optional[tuple[float, "cv2.Mat"]]:
        with self._lock:
            return self.latest

    def get_preroll(self) -> list[tuple[float, "cv2.Mat"]]:
        """Snapshot of the pre-roll buffer (oldest first)."""
        with self._lock:
            return list(self.buffer)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.cap is not None:
            self.cap.release()
