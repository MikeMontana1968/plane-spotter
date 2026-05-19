"""Incident state machine and per-incident folder management.

States:
    IDLE        -> nothing happening
    ACTIVE      -> writing frames into a per-incident folder
    COOLDOWN    -> just finished an incident, ignore motion briefly to avoid
                   double-counting the same aircraft due to a brief mask gap

Transitions:
    IDLE -> ACTIVE     after N consecutive frames with motion
    ACTIVE -> COOLDOWN when no motion seen for `incident_end_quiet_seconds`
                       OR incident exceeds `max_incident_seconds`
    COOLDOWN -> IDLE   after `cooldown_seconds`

On entering ACTIVE, the pre-roll buffer is dumped into the incident folder so
we capture the aircraft entering the frame. On exit, a record is appended to
incidents.jsonl for the stats module to consume.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2

logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    COOLDOWN = "cooldown"


# Timestamp overlay: ~12pt bold sans-serif, bottom-right.
# OpenCV has no Arial; HERSHEY_DUPLEX is the closest sans bundled with cv2.
# scale 0.5 ~ 12px cap height; thickness 2 gives the bold weight.
_TS_FONT = cv2.FONT_HERSHEY_DUPLEX
_TS_SCALE = 0.5
_TS_THICKNESS = 2
_TS_MARGIN = 10


def _draw_timestamp(frame, ts: float) -> None:
    text = datetime.fromtimestamp(ts).strftime("%H:%M:%S %a %d-%b")
    (tw, th), baseline = cv2.getTextSize(text, _TS_FONT, _TS_SCALE, _TS_THICKNESS)
    h, w = frame.shape[:2]
    x = w - tw - _TS_MARGIN
    y = h - _TS_MARGIN - baseline
    # Black outline for readability against bright sky, then white fill.
    cv2.putText(frame, text, (x, y), _TS_FONT, _TS_SCALE, (0, 0, 0), _TS_THICKNESS + 2, cv2.LINE_AA)
    cv2.putText(frame, text, (x, y), _TS_FONT, _TS_SCALE, (255, 255, 255), _TS_THICKNESS, cv2.LINE_AA)


class IncidentManager:
    def __init__(self, config, audio=None):
        self.config = config
        self.audio = audio  # AudioMonitor, optional — used for per-incident WAV
        self.state = State.IDLE
        self.current_dir: Optional[Path] = None
        self.incident_start: Optional[float] = None
        self.last_motion: Optional[float] = None
        self.last_audio: Optional[float] = None
        self.cooldown_until: float = 0.0
        self.frame_count: int = 0
        self.frames_since_save: int = 0
        self.consecutive_motion_frames: int = 0
        self._audio_seen: bool = False
        self.config.output_dir.mkdir(exist_ok=True, parents=True)

    # -- public API -------------------------------------------------------

    def update(
        self,
        ts: float,
        frame,
        has_motion: bool,
        preroll: list,
        blobs: list,
        audio_hot: bool = False,
    ) -> None:
        if self.state is State.COOLDOWN and ts >= self.cooldown_until:
            self.state = State.IDLE
            self.consecutive_motion_frames = 0

        if self.state is State.IDLE:
            if audio_hot and self.config.audio_triggers_incident:
                # Audio alone is sufficient — the rumble lags the visual pass
                # by 3-5s, so the preroll buffer already contains the jet.
                self._start_incident(ts, preroll)
                self._audio_seen = True
            elif has_motion:
                self.consecutive_motion_frames += 1
                threshold = self.config.motion_confirmation_frames
                if audio_hot:
                    threshold = self.config.audio_boosted_confirmation_frames
                if self.consecutive_motion_frames >= threshold:
                    self._start_incident(ts, preroll)
                    if audio_hot:
                        self._audio_seen = True
            else:
                self.consecutive_motion_frames = 0

        if self.state is State.ACTIVE:
            if audio_hot:
                self._audio_seen = True
                self.last_audio = ts
            self._save_frame(ts, frame, blobs)
            if has_motion:
                self.last_motion = ts
            quiet_motion = ts - (self.last_motion or ts)
            quiet_audio = ts - (self.last_audio or ts)
            elapsed = ts - (self.incident_start or ts)
            # End only when BOTH signals have been quiet for the threshold —
            # otherwise an audio-triggered incident would cut off after 3s
            # while the rumble was still going.
            both_quiet = (
                quiet_motion >= self.config.incident_end_quiet_seconds
                and quiet_audio >= self.config.incident_end_quiet_seconds
            )
            if both_quiet or elapsed >= self.config.max_incident_seconds:
                self._end_incident(ts)

    # -- internals --------------------------------------------------------

    def _start_incident(self, ts: float, preroll: list) -> None:
        stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d_%H-%M-%S")
        self.current_dir = self.config.output_dir / stamp
        # If somehow the folder already exists (same-second restart), suffix it.
        suffix = 1
        base = self.current_dir
        while self.current_dir.exists():
            self.current_dir = base.with_name(f"{base.name}_{suffix}")
            suffix += 1
        self.current_dir.mkdir(parents=True)

        self.state = State.ACTIVE
        self.incident_start = ts
        self.last_motion = ts
        self.last_audio = ts
        self.frame_count = 0
        self.frames_since_save = 0
        self._audio_seen = False

        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.config.jpeg_quality]
        for i, (pts, pframe) in enumerate(preroll):
            stamped = pframe.copy()
            _draw_timestamp(stamped, pts)
            fname = self.current_dir / f"preroll_{i:04d}.jpg"
            cv2.imwrite(str(fname), stamped, encode_params)

        if self.audio is not None and self.config.save_incident_audio:
            self.audio.start_incident_recording()

        logger.info("incident started -> %s", self.current_dir.name)

    def _save_frame(self, ts: float, frame, blobs: list) -> None:
        self.frames_since_save += 1
        if self.frames_since_save < self.config.save_every_n_frames:
            return
        self.frames_since_save = 0
        annotated = frame.copy()
        for (x, y, w, h, _area) in blobs:
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
        _draw_timestamp(annotated, ts)
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.config.jpeg_quality]
        fname = self.current_dir / f"frame_{self.frame_count:04d}.jpg"
        cv2.imwrite(str(fname), annotated, encode_params)
        self.frame_count += 1

    def _end_incident(self, ts: float) -> None:
        duration = ts - (self.incident_start or ts)

        if (
            self.audio is not None
            and self.config.save_incident_audio
            and self.current_dir is not None
        ):
            self.audio.stop_incident_recording_and_save(self.current_dir / "audio.wav")

        record = {
            "start": self.incident_start,
            "end": ts,
            "start_iso": datetime.fromtimestamp(self.incident_start or ts).isoformat(),
            "duration_s": round(duration, 2),
            "folder": self.current_dir.name if self.current_dir else None,
            "frame_count": self.frame_count,
            "audio_present": self._audio_seen,
        }
        with open(self.config.incidents_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        logger.info(
            "incident ended <- %s (%.1fs, %d saved frames, audio=%s)",
            record["folder"], duration, self.frame_count, self._audio_seen,
        )
        self.state = State.COOLDOWN
        self.cooldown_until = ts + self.config.cooldown_seconds
        self.current_dir = None
        self.incident_start = None
        self.last_motion = None
        self.last_audio = None
