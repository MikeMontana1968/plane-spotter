"""Rolling incident counts.

incidents.jsonl is the source of truth — append-only, one JSON object per
line. We re-read it on demand to compute counts. For typical loads (a few
hundred incidents/day) this is fine; if it ever gets huge, swap for SQLite.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def load_incidents(path: Path) -> list[dict]:
    if not path.exists():
        return []
    incidents: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                incidents.append(json.loads(line))
            except json.JSONDecodeError:
                # Malformed line — skip but keep going.
                continue
    return incidents


def rolling_counts(incidents: list[dict], now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    windows = {"1h": 3600, "8h": 8 * 3600, "24h": 24 * 3600}
    counts: dict = {"total": len(incidents)}
    for label, secs in windows.items():
        cutoff = now - secs
        counts[label] = sum(1 for i in incidents if i.get("start", 0) >= cutoff)
    return counts
