# CLAUDE.md

Context for resuming work on plane-spotter in VS Code with Claude Code.

## What this is

A personal-project Python app that watches the sky through a USB webcam and
logs incidents whenever something flies through the field of view. Target
environment is a laptop in Sorrento Valley pointed at the Miramar approach
corridor — fighter jets, Ospreys, the occasional commercial airliner. Not a
work project, no defense/aerospace coupling.

The webcam is intended to be a GoPro Hero 9 or later in USB webcam mode
(natively supported, no capture card). All-day continuous use overheats
GoPros, so plan on either media-mod airflow or accept that uptime will be
limited until a more durable camera replaces it.

## Goals

1. Count aircraft passes per day, with rolling 1h / 8h / 24h windows and a
   "since started" total.
2. Save a folder of JPEGs per incident so review/labeling is possible after
   the fact.
3. Be cheap enough to leave running on a laptop indefinitely (no GPU
   required for the baseline).

## Architecture

```
                +-----------+        +-----------------+
   GoPro USB -> | FrameSrc  | -----> |  MotionDetector | ---+
                | (thread)  |  frame |  (MOG2 + blobs) |    |
                +-----------+        +-----------------+    |
                       |                                    v
                       |  preroll                +---------------------+
                       +-----------------------> |  IncidentManager    |
                                                 |  (IDLE/ACTIVE/CD)   |
                                                 +---------------------+
                                                          |
                                                          v
                                          incidents/<timestamp>/*.jpg
                                          incidents.jsonl

  AudioMonitor (stub) -> will gate / boost-confidence the IncidentManager
```

- **Capture**: `cv2.VideoCapture` on its own thread, pushes the latest frame
  into a `deque` ring buffer sized to `preroll_seconds * target_fps`.
- **Detection**: MOG2 background subtraction → morphology → contour
  filtering by area and aspect ratio. Sky's near-uniform background makes
  this much easier than typical security-cam motion detection.
- **Incidents**: simple state machine. On `IDLE → ACTIVE` we dump the
  pre-roll buffer; while `ACTIVE` we save annotated frames every Nth tick;
  we end after `incident_end_quiet_seconds` of no motion (or a hard
  `max_incident_seconds` cap), then sit in `COOLDOWN` to avoid double-
  counting the same target.
- **Stats**: `incidents.jsonl` is the source of truth. Re-read on demand;
  the rolling counts are just timestamp filters.

## File layout

```
plane-spotter/
├── pyproject.toml          # uv-managed; opencv + numpy core; audio/yolo extras
├── .python-version         # 3.11
├── .gitignore              # ignores incidents/, incidents.jsonl
├── README.md
├── CLAUDE.md               # this file
├── src/plane_spotter/
│   ├── __init__.py
│   ├── __main__.py         # `python -m plane_spotter`
│   ├── config.py           # ALL tunables; dataclass
│   ├── capture.py          # FrameSource (threaded, ring buffer)
│   ├── detector.py         # MotionDetector (MOG2 + blob filtering)
│   ├── incident.py         # IncidentManager + State enum
│   ├── stats.py            # load_incidents / rolling_counts
│   ├── audio.py            # AudioMonitor STUB
│   └── main.py             # run() orchestrator
└── incidents/              # output, gitignored
```

## Running

```powershell
# initial setup
cd C:\Users\mikem\plane-spotter
uv sync                     # creates .venv, installs core deps

# normal run
uv run plane-spotter
# or equivalently:
uv run python -m plane_spotter
```

To enable optional extras later:

```powershell
uv sync --extra yolo        # ultralytics
```

(sounddevice and scipy are core deps — audio is always on.)

## Current state

- ✅ Threaded capture with pre-roll
- ✅ Motion detection
- ✅ Incident state machine + folder output
- ✅ Rolling stats from incidents.jsonl
- ✅ Live preview window with bounding boxes
- 🚧 Audio trigger — module scaffolded, not implemented
- 🚧 Object verification — no YOLO integration yet
- 🚧 Web dashboard — none yet, stats only print to console

## Tuning checklist (first run)

These parameters in `config.py` will need real-world tuning:

| Field | Likely adjustment |
|---|---|
| `camera_index` | 0/1/2 until the GoPro opens |
| `capture_width`/`_height` | match GoPro webcam-mode resolution |
| `min_blob_area`/`max_blob_area` | watch preview, set bounds around real planes |
| `motion_confirmation_frames` | raise if false starts from cloud edges |
| `incident_end_quiet_seconds` | raise if planes are getting split into multiple incidents |
| `cooldown_seconds` | raise if same plane double-counts |

The fastest tuning loop: run, point at sky, watch the bounding boxes appear
on the preview, adjust `min_blob_area` / `max_blob_area` until birds and
clouds are mostly rejected and aircraft consistently trigger.

## Roadmap

### Near-term
1. Implement `AudioMonitor`. Bandpass 50–300 Hz RMS via scipy, expose
   `audio_hot` flag with hysteresis. Use it to lower
   `motion_confirmation_frames` while audio is hot, and tag each incident
   with `audio_present: bool`.
2. Add a simple centroid tracker. A real plane has a roughly linear
   trajectory across many frames; cloud edges and birds don't. Reject
   blobs that don't move consistently.
3. Add `tools/review.py` — small CLI to walk `incidents/`, show the best
   frame, prompt y/n/skip, and write labels to a sidecar JSON. Useful for
   building a tuning ground-truth set.

### Medium-term
4. Optional YOLO post-pass on saved incident folders. Use the largest /
   most-centered crop and run YOLOv8 (`airplane` class, COCO). Tag
   incidents with confidence scores; don't put YOLO in the hot path.
5. Tiny Flask dashboard at `localhost:8080` showing rolling counts, last
   N incident thumbnails, current state. Replaces the printed `[stats]`
   line.
6. ADS-B correlation. Cross-reference incident timestamps with a local
   dump1090 feed (or OpenSky API) to label commercial flights vs.
   military (which won't broadcast). This is the killer feature — it
   essentially auto-labels half the dataset.

### Long-term / nice-to-have
7. Consider switching the capture device away from the GoPro to something
   built for continuous duty (a cheap industrial USB camera or an IP
   camera over RTSP). The capture module already isolates this concern.
8. Train a tiny CNN classifier on the labeled incident folders for
   "fighter / commercial / helicopter / bird / nope" to replace the
   coarse YOLO pass.

## Conventions

- Python 3.11+, type hints where they help readability (not exhaustive).
- All tunables live in `config.py` as a `Config` dataclass. No magic
  numbers in business logic.
- PowerShell for any docs / examples (Mike's on Windows).
- `incidents.jsonl` schema is append-only; if it changes, write a
  `migrate_incidents_log.py` rather than rewriting in place.

## Known gotchas

- `cv2.CAP_PROP_FPS` is a hint, not a contract; the camera's actual rate
  is what governs the loop.
- GoPro USB webcam mode requires the GoPro Webcam desktop helper on
  Windows for Hero 8; Hero 9+ exposes UVC natively. If `camera_index`
  doesn't find it, check Device Manager.
- The OpenCV preview window swallows the terminal's Ctrl+C on Windows
  occasionally. Closing the preview window or pressing `q` is more
  reliable.
- `cv2.imshow` from a non-main thread will silently fail on some
  builds — keep all UI on the main thread (currently is).
