"""Configuration for plane-spotter.

All tunables live here. Adjust based on your camera, framing, and the angular
size of typical targets. Sensible defaults for a 1080p capture pointed at sky.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # ---- Camera ---------------------------------------------------------
    # Index passed to cv2.VideoCapture. On Windows, GoPro Webcam usually
    # registers as the first/second device. Try 0, 1, 2 if the wrong cam opens.
    camera_index: int = 1
    # On Windows, forcing the DirectShow backend tends to be more reliable
    # than the default MSMF for non-Microsoft cameras (GoPro, Elgato, etc.).
    use_dshow_backend: bool = True
    capture_width: int = 1920
    capture_height: int = 1080
    target_fps: int = 15

    # ---- Pre-roll buffer ------------------------------------------------
    # How many seconds of frames to retain before an incident starts so we
    # don't miss the entry into frame. Sized to cover the 3-5s acoustic lag
    # between a jet passing overhead and the rumble reaching the mic.
    preroll_seconds: float = 7.0

    # ---- Motion detection -----------------------------------------------
    # Gaussian blur kernel size (odd integer) applied before MOG2. Larger
    # values smear high-frequency texture (mesh, foliage) so the background
    # model isn't constantly chasing per-pixel flicker. 5 is the OpenCV
    # default; bump to 11 or 15 if the preview is busy with fixed texture.
    detector_blur_kernel: int = 11 # 5
    mog2_history: int = 1000 # 500
    mog2_var_threshold: float = 80 # 25.0
    # Tune these for your angular target size. Aircraft at ~5 km look like
    # 5–30 px blobs at 1080p with a typical webcam FOV. Birds will be smaller
    # and erratic; clouds larger and slow-moving.
    min_blob_area: int = 80 # 20
    max_blob_area: int = 8000
    # Aspect ratio guard — reject very tall or very wide blobs (lens flare,
    # power lines moving in wind). 0 disables.
    max_aspect_ratio: float = 8.0

    # ---- Incident state machine ----------------------------------------
    # Need motion detected this many frames in a row before we open an
    # incident. Filters single-frame noise spikes.
    motion_confirmation_frames: int = 3
    # End the incident if no motion is seen for this long.
    incident_end_quiet_seconds: float = 3.0
    # After an incident ends, wait this long before a new one can start.
    cooldown_seconds: float = 10.0
    # Hard cap so a stuck-on detection doesn't fill the disk.
    max_incident_seconds: float = 60.0

    # ---- Output ---------------------------------------------------------
    output_dir: Path = field(default_factory=lambda: Path("incidents"))
    incidents_log: Path = field(default_factory=lambda: Path("incidents.jsonl"))
    # 1 = save every frame during incident, 2 = every other, etc.
    # 7 at target_fps=15 ~ one frame every 470ms.
    save_every_n_frames: int = 7
    # JPEG quality 0-100. 90 is a reasonable balance.
    jpeg_quality: int = 90

    # ---- UI -------------------------------------------------------------
    show_preview: bool = True
    print_stats_every_seconds: float = 60.0

    # ---- Logging --------------------------------------------------------
    log_file: Path = field(default_factory=lambda: Path("plane-spotter.log"))
    log_level: str = "INFO"
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5

    # ---- Centroid tracker ----------------------------------------------
    # Greedy nearest-centroid tracker. A blob's track is "qualified" — i.e.
    # accepted as real aircraft motion — when it has accumulated enough
    # points, moved far enough, and travelled close to a straight line.
    # Birds zig-zag; cloud edges crawl; jets do neither.
    track_max_match_distance_px: float = 80.0
    track_stale_seconds: float = 1.5
    track_min_points: int = 5
    track_min_displacement_px: float = 50.0
    # displacement / path_length. 1.0 = perfectly straight.
    track_min_linearity: float = 0.85
    # Cap stored points per track to bound memory.
    track_max_points: int = 120

    # ---- Audio trigger ---------------------------------------------------
    # Set False to disable audio monitoring even if deps are installed.
    audio_enabled: bool = True
    # Microphone device index for sounddevice. None = system default.
    audio_device: int | None = None
    # Sample rate in Hz. 22050 is enough for our 50-300 Hz band of interest.
    audio_sample_rate: int = 22050
    # Block duration in seconds. Each callback receives this much audio.
    audio_block_seconds: float = 0.1
    # Bandpass filter edges in Hz (jet rumble band).
    audio_low_hz: float = 50.0
    audio_high_hz: float = 300.0
    # Bandpass filter order (Butterworth).
    audio_filter_order: int = 4
    # How many times above the noise floor RMS must be to trigger "hot".
    audio_threshold: float = 3.0
    # EMA smoothing factor for the noise floor. Smaller = slower adaptation.
    # 0.002 at 0.1s blocks ~ 50-second time constant.
    audio_noise_floor_alpha: float = 0.002
    # Number of initial blocks to skip before allowing triggers (warm-up).
    # At 0.1s blocks, 50 = 5 seconds of silence to establish baseline.
    audio_warmup_blocks: int = 50
    # When audio_hot, lower motion_confirmation_frames to this value.
    audio_boosted_confirmation_frames: int = 1
    # If True, audio_hot alone opens an incident even without motion. The
    # preroll buffer covers the acoustic lag, so the jet's visual pass is
    # still captured from before the rumble arrived.
    audio_triggers_incident: bool = True
    # Interval for periodic audio summary log lines (peak ratio, mean RMS,
    # noise floor, hot fraction). Each hot/cold transition is logged
    # separately, regardless of this interval.
    audio_log_summary_seconds: float = 60.0
