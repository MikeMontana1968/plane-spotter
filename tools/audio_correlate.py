"""One-shot analysis: correlate user-reported jet times with audio HOT events.

Parses plane-spotter.log, extracts every `audio HOT` line with its peak
ratio, then compares activity inside ±window-minutes around each reported
time vs. the rest of the day.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / "plane-spotter.log"
DAY = "2026-05-15"  # only correlate today's events
WINDOW_MIN = 3      # ± minutes around each reported time

# User's casual notes of times they heard a jet overhead.
# 12:08-12:18 cluster reads as PM since 3:30/4:15 are PM; treat all post-noon
# as PM (so 12:08 == 12:08, 3:30 == 15:30, 4:15 == 16:15).
REPORTED = [
    "08:42", "09:30", "10:05", "10:09",
    "11:45", "12:08", "12:12", "12:18",
    "15:30", "16:15",
]

HOT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*audio HOT .*ratio=([\d.]+)x"
)
INCIDENT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) .*incident started"
)


def parse() -> tuple[list[tuple[datetime, float]], list[datetime]]:
    hots: list[tuple[datetime, float]] = []
    incidents: list[datetime] = []
    with LOG.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith(DAY):
                continue
            m = HOT_RE.match(line)
            if m:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                hots.append((ts, float(m.group(2))))
                continue
            m = INCIDENT_RE.match(line)
            if m:
                incidents.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    return hots, incidents


def in_any_window(ts: datetime, centers: list[datetime], window: timedelta) -> bool:
    return any(abs(ts - c) <= window for c in centers)


def summarize(label: str, ratios: list[float]) -> str:
    if not ratios:
        return f"{label}: 0 events"
    return (
        f"{label}: n={len(ratios):5d}  "
        f"mean={statistics.mean(ratios):6.2f}x  "
        f"median={statistics.median(ratios):6.2f}x  "
        f"p90={sorted(ratios)[int(len(ratios)*0.9)]:6.2f}x  "
        f"max={max(ratios):7.2f}x"
    )


def main() -> None:
    hots, incidents = parse()
    centers = [datetime.strptime(f"{DAY} {t}:00", "%Y-%m-%d %H:%M:%S")
               for t in REPORTED]
    window = timedelta(minutes=WINDOW_MIN)

    in_win, out_win = [], []
    for ts, ratio in hots:
        (in_win if in_any_window(ts, centers, window) else out_win).append(ratio)

    print(f"=== Day {DAY}, audio HOT events: {len(hots)}, incidents: {len(incidents)} ===\n")
    print(f"Reference times: {REPORTED}  (±{WINDOW_MIN} min windows)\n")
    print(summarize("INSIDE  windows", in_win))
    print(summarize("OUTSIDE windows", out_win))
    print()

    print("--- Per-window detail (HOT events within ±3 min of each reported time) ---")
    print(f"{'reported':>10}  {'#HOT':>5}  {'maxRatio':>9}  "
          f"{'meanRatio':>10}  {'#incidents':>11}  closest_incident_offset")
    for c in centers:
        win_hots = [r for ts, r in hots if abs(ts - c) <= window]
        win_inc = [i for i in incidents if abs(i - c) <= window]
        closest = min((abs(i - c) for i in incidents), default=None)
        closest_str = f"{int(closest.total_seconds())}s" if closest else "n/a"
        print(
            f"{c.strftime('%H:%M'):>10}  {len(win_hots):>5}  "
            f"{(max(win_hots) if win_hots else 0):>9.2f}  "
            f"{(statistics.mean(win_hots) if win_hots else 0):>10.2f}  "
            f"{len(win_inc):>11}  {closest_str:>10}"
        )

    print()
    print("--- HOT-rate by hour (events / minute), tagged * if hour contains a reference time ---")
    ref_hours = {c.hour for c in centers}
    by_hour: dict[int, list[float]] = defaultdict(list)
    for ts, r in hots:
        by_hour[ts.hour].append(r)
    for h in sorted(by_hour):
        marker = "*" if h in ref_hours else " "
        rs = by_hour[h]
        print(
            f"  {marker} {h:02d}:00  n={len(rs):4d}  "
            f"rate={len(rs)/60:5.1f}/min  "
            f"median={statistics.median(rs):5.2f}x  max={max(rs):6.2f}x"
        )


if __name__ == "__main__":
    main()
