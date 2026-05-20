# plane-spotter

Webcam-based aircraft incident detector. Watches the sky, opens an "incident"
folder per moving target, drops in pre-roll + active-frame JPEGs and a
matching `audio.wav`, and tracks rolling counts (1h / 8h / 24h / total).

Built around a Logitech C920 (camera **and** mic), pointed at the Miramar
approach corridor over Sorrento Valley.

## Quick start

```powershell
# from the project root
uv sync
uv run plane-spotter
```

Press `q` in the preview window or `Ctrl+C` in the terminal to stop.

## What's implemented

- **Capture** — threaded `cv2.VideoCapture`, camera selected by case-insensitive
  name substring (default `"C920"`), MJPG forced where DSHOW negotiates a bad
  pixel format, 7-second pre-roll ring buffer
- **Motion detection** — MOG2 background subtraction with Gaussian pre-blur,
  contour filtering by area and aspect ratio
- **Centroid tracker** — greedy nearest-neighbour association, with track
  qualification on linearity, displacement, and point count. Designed to
  reject birds and cloud edges (non-linear paths) while passing aircraft
  (near-straight crosses over many seconds)
- **Incident state machine** — IDLE → ACTIVE → COOLDOWN; pre-roll dumped on
  entry, periodic active frames, ends after a quiet period
- **Audio trigger** — `sounddevice` + `scipy` Butterworth bandpass (80–200 Hz
  currently per `config.py`; the `audio.py` module docstring says 50–300
  but config wins), RMS vs. exponentially-smoothed noise floor, hot/cold
  transition logging, per-incident `audio.wav` saved alongside frames
- **Rolling stats** — `incidents.jsonl` is the source of truth; counts are
  re-derived on demand
- **Live preview** — bounding boxes + state overlay (main-thread `imshow`)

## What's not yet

- YOLO object verification (post-pass on saved incidents)
- ADS-B correlation against dump1090 / OpenSky (auto-label commercial vs.
  military)
- Web dashboard

## Deployment plan

**Current site:** car parked in a lot in Sorrento Valley, roughly between
home and Miramar. No AC power. Short-term: USB-C PD car charger off the
12V socket. Cabin heat managed by leaving the sun roof mostly open.

**Planned migration:** Raspberry Pi 4/5 + C920. ~5W draw runs indefinitely
off 12V via a buck converter — no inverter losses, tolerates cabin heat
much better than a laptop. `capture.py` already isolates the camera so the
port is mostly mechanical.

See [FIELD-USE.md](FIELD-USE.md) for the pre-deploy checklist (Windows
power settings, USB selective suspend, mic permissions).

## Trial progress

**2026-05-19** — first real car-lot session. Ran 07:35 → ~10:00, then the
laptop died on battery. 223 incidents logged. Spot-check by listening to
the saved pre-roll audio:

- Dominant **visual** trigger: birds. The centroid tracker's linearity
  filter is the right tool for this; tuning ongoing.
- Dominant **audio** trigger: people talking in the parking lot. The
  current 80–200 Hz bandpass still overlaps voice fundamentals (~85–255 Hz).
  Plan is to capture a confirmed jet pre-roll at the parking lot first
  (the rumble is body-felt there in the right reflection spot), look at
  the spectrum, and tune from data rather than theory.
- Pre-roll audio is genuinely useful for review — `audio.wav` per incident
  stays.

## Tools

Utilities for field setup and live diagnosis (all under `tools/`):

- `test_mic.py` — sanity-check that the configured mic returns non-zero RMS
- `list_audio.py` — enumerate available audio devices
- `listen_live.py` — capture N seconds and show what the bandpass +
  threshold logic sees in real time. Use during a known flyover to gut-check
  the audio path.
- `audio_correlate.py` — offline analysis of recorded incident audio

## Tuning

All tunables live in `src/plane_spotter/config.py` as a `Config` dataclass.
Fields most worth touching first:

- `camera_name` — substring of the DirectShow device's friendly name
- `min_blob_area` / `max_blob_area` — set after watching real targets in
  the preview
- `motion_confirmation_frames` — bump if cloud edges trigger false starts
- `track_min_linearity` / `track_min_points` — tighten to reject more birds,
  loosen if real aircraft are being dropped
- `cooldown_seconds` — bump if a single plane is being counted twice
- `audio_low_hz` / `audio_high_hz` — pending real jet-spectrum capture

## Output layout

```
incidents/
  2026-05-19_07-35-42/
    preroll_0000.jpg
    preroll_0001.jpg
    ...
    frame_0000.jpg
    ...
    audio.wav
incidents.jsonl   # one JSON record per ended incident
plane-spotter.log # rotating app log
```

See [CLAUDE.md](CLAUDE.md) for architecture notes and the longer-form roadmap.
