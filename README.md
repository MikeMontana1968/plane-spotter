# plane-spotter

Webcam-based aircraft incident detector. Watches the sky, opens an "incident"
folder per moving target, drops in pre-roll + active-frame JPEGs, and tracks
rolling counts (1h / 8h / 24h / total).

Built with sky-pointed webcams in mind — currently a GoPro USB-webcam feed
with a Sorrento Valley / Miramar view.

## Quick start

```powershell
# from the project root
uv sync
uv run plane-spotter
```

Press `q` in the preview window or `Ctrl+C` in the terminal to stop.

## What's in the box

- Threaded frame capture with a 5-second pre-roll ring buffer
- MOG2 background-subtraction motion detection with blob-area + aspect-ratio filtering
- Incident state machine (IDLE → ACTIVE → COOLDOWN) writing per-incident folders
- Rolling stats from `incidents.jsonl`
- Audio trigger module (stub — not wired in yet)

## Tuning

Open `src/plane_spotter/config.py`. The fields most worth touching first:

- `camera_index` — try 0, 1, 2 if the wrong camera opens
- `min_blob_area` / `max_blob_area` — set after seeing what your sky+target combo produces
- `motion_confirmation_frames` — bump if you're getting noise-spike false starts
- `cooldown_seconds` — bump if the same plane is being counted twice

## Output layout

```
incidents/
  2026-05-07_14-32-15/
    preroll_0000.jpg
    preroll_0001.jpg
    ...
    frame_0000.jpg
    frame_0001.jpg
    ...
incidents.jsonl   # one JSON record per ended incident
```

See `CLAUDE.md` for architecture notes and the roadmap.
