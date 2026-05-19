"""Main orchestration loop.

Pulls frames from the (threaded) FrameSource, runs the MotionDetector,
feeds blobs through the centroid Tracker, and only treats motion as "real"
once a track is qualified (enough points, enough displacement, linear
trajectory). Audio remains an independent trigger covering the case where
the aircraft has already left frame by the time the rumble arrives.

Press 'q' in the preview window or Ctrl+C in the terminal to quit.
"""

from __future__ import annotations

import logging
import logging.handlers
import signal
import time

import cv2

from .audio import AudioMonitor
from .capture import FrameSource
from .config import Config
from .detector import MotionDetector
from .incident import IncidentManager, State
from .stats import load_incidents, rolling_counts
from .tracker import Tracker

logger = logging.getLogger(__name__)


def _setup_logging(config: Config) -> None:
    root = logging.getLogger("plane_spotter")
    if root.handlers:
        return
    root.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_file,
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.propagate = False


def run() -> None:
    config = Config()
    _setup_logging(config)

    src = FrameSource(config)
    src.start()
    detector = MotionDetector(config)
    tracker = Tracker(config)
    audio = AudioMonitor(config)
    audio.start()
    incidents = IncidentManager(config, audio=audio)

    stop_flag = {"v": False}

    def _handle_sigint(_signum, _frame):
        stop_flag["v"] = True

    signal.signal(signal.SIGINT, _handle_sigint)

    logger.info("plane-spotter running. Press 'q' in preview or Ctrl+C to quit.")

    last_processed_ts = 0.0
    last_stats_print = 0.0

    try:
        while not stop_flag["v"]:
            sample = src.get_latest()
            if sample is None or sample[0] == last_processed_ts:
                time.sleep(0.005)
                if config.show_preview:
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                continue
            ts, frame = sample
            last_processed_ts = ts

            blobs, _mask = detector.detect(frame)
            tracker.update(ts, blobs)
            qualified = tracker.qualified()
            has_qualified_track = bool(qualified)

            preroll = src.get_preroll() if incidents.state is State.IDLE else []
            incidents.update(
                ts, frame, has_qualified_track, preroll, blobs, audio.audio_hot
            )

            if config.show_preview:
                preview = frame.copy()
                qualified_ids = {t.id for t in qualified}
                # Map each blob to its track (if any) so we can colour it.
                for (x, y, w, h, _a) in blobs:
                    cx, cy = x + w / 2.0, y + h / 2.0
                    colour = (0, 255, 0)
                    for t in tracker.tracks.values():
                        if not t.points:
                            continue
                        _, tx, ty = t.points[-1]
                        if abs(tx - cx) < 1 and abs(ty - cy) < 1:
                            colour = (0, 255, 255) if t.id in qualified_ids else (180, 180, 180)
                            break
                    cv2.rectangle(preview, (x, y), (x + w, y + h), colour, 2)
                # Draw qualified track polylines.
                for t in qualified:
                    pts = [(int(p[1]), int(p[2])) for p in t.points]
                    for i in range(1, len(pts)):
                        cv2.line(preview, pts[i - 1], pts[i], (0, 255, 255), 1)
                audio_label = " [AUDIO]" if audio.audio_hot else ""
                label = (
                    f"state: {incidents.state.value}  "
                    f"blobs: {len(blobs)}  "
                    f"tracks: {len(tracker.tracks)}  "
                    f"qual: {len(qualified)}{audio_label}"
                )
                cv2.putText(
                    preview, label, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
                )
                cv2.imshow("plane-spotter", preview)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            now = time.time()
            if now - last_stats_print > config.print_stats_every_seconds:
                counts = rolling_counts(load_incidents(config.incidents_log), now=now)
                logger.info("stats %s", counts)
                last_stats_print = now
    finally:
        src.stop()
        audio.stop()
        if config.show_preview:
            cv2.destroyAllWindows()
        logger.info("plane-spotter stopped.")


if __name__ == "__main__":
    run()
