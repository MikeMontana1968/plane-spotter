"""Greedy centroid tracker for rejecting non-aircraft motion.

Each call to `update(ts, blobs)` associates the latest blob centroids with
existing tracks via greedy nearest-neighbour matching (capped by
`track_max_match_distance_px`). Unmatched blobs spawn fresh tracks; tracks
that go unobserved for `track_stale_seconds` are dropped.

A track is "qualified" once it has accumulated enough points, moved far
enough, and stayed close to a straight line — defined as
displacement / path_length above `track_min_linearity`. Aircraft satisfy
all three; birds and cloud edges do not.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Track:
    id: int
    points: list[tuple[float, float, float]] = field(default_factory=list)
    last_seen: float = 0.0

    @property
    def length(self) -> int:
        return len(self.points)

    def displacement(self) -> float:
        if len(self.points) < 2:
            return 0.0
        _, x0, y0 = self.points[0]
        _, x1, y1 = self.points[-1]
        return math.hypot(x1 - x0, y1 - y0)

    def path_length(self) -> float:
        total = 0.0
        for i in range(1, len(self.points)):
            _, x0, y0 = self.points[i - 1]
            _, x1, y1 = self.points[i]
            total += math.hypot(x1 - x0, y1 - y0)
        return total

    def linearity(self) -> float:
        path = self.path_length()
        if path < 1.0:
            return 0.0
        return self.displacement() / path

    def is_qualified(self, config) -> bool:
        if self.length < config.track_min_points:
            return False
        if self.displacement() < config.track_min_displacement_px:
            return False
        return self.linearity() >= config.track_min_linearity


class Tracker:
    def __init__(self, config):
        self.config = config
        self.tracks: dict[int, Track] = {}
        self._ids = itertools.count()

    def update(self, ts: float, blobs: list) -> list[Track]:
        centroids = [
            (x + w / 2.0, y + h / 2.0) for (x, y, w, h, _a) in blobs
        ]

        stale = [
            tid for tid, t in self.tracks.items()
            if ts - t.last_seen > self.config.track_stale_seconds
        ]
        for tid in stale:
            del self.tracks[tid]

        max_d = self.config.track_max_match_distance_px
        candidates: list[tuple[float, int, int]] = []
        for tid, track in self.tracks.items():
            if not track.points:
                continue
            _, tx, ty = track.points[-1]
            for j, (cx, cy) in enumerate(centroids):
                d = math.hypot(tx - cx, ty - cy)
                if d <= max_d:
                    candidates.append((d, tid, j))
        candidates.sort()

        matched_tracks: set[int] = set()
        matched_blobs: set[int] = set()
        for _d, tid, j in candidates:
            if tid in matched_tracks or j in matched_blobs:
                continue
            matched_tracks.add(tid)
            matched_blobs.add(j)
            cx, cy = centroids[j]
            track = self.tracks[tid]
            track.points.append((ts, cx, cy))
            track.last_seen = ts
            cap = self.config.track_max_points
            if cap and len(track.points) > cap:
                track.points = track.points[-cap:]

        for j, (cx, cy) in enumerate(centroids):
            if j in matched_blobs:
                continue
            tid = next(self._ids)
            self.tracks[tid] = Track(
                id=tid, points=[(ts, cx, cy)], last_seen=ts
            )

        return list(self.tracks.values())

    def qualified(self) -> list[Track]:
        return [t for t in self.tracks.values() if t.is_qualified(self.config)]
