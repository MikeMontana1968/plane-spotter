"""Audio trigger — bandpass RMS monitoring for jet rumble detection.

Opens a sounddevice InputStream (mono, 22050 Hz, 0.1s blocks), applies a
Butterworth bandpass filter (50-300 Hz), computes RMS, and compares against
an exponentially-smoothed noise floor estimate. When RMS exceeds
noise_floor * threshold, the `audio_hot` flag goes True.

Requires optional deps: `uv sync --extra audio` (sounddevice, scipy).
If not installed, the monitor stays inert — start()/stop() are no-ops and
audio_hot is always False.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    from scipy.signal import butter, sosfilt, sosfilt_zi

    _AUDIO_AVAILABLE = True
except ImportError:
    _AUDIO_AVAILABLE = False


def _resolve_audio_device(name: str) -> tuple[int, str]:
    """Find the first input device whose name contains `name` (case-insensitive).

    Returns (index, device_name). Raises RuntimeError listing the available
    input devices if no match is found.
    """
    needle = name.lower()
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and needle in d["name"].lower():
            return i, d["name"]
    listing = "\n  ".join(
        f"[{i}] {d['name']}"
        for i, d in enumerate(devices) if d["max_input_channels"] > 0
    ) or "(none)"
    raise RuntimeError(
        f"No input device matched name '{name}'. Available input devices:\n  {listing}"
    )


class AudioMonitor:
    """Monitors microphone input for low-frequency rumble (aircraft noise)."""

    def __init__(self, config):
        self.config = config

        # Public state — read from main thread via properties
        self._audio_hot = False
        self._last_hot_ts = 0.0
        self._lock = threading.Lock()

        # Internal state — written only from the callback thread
        self._stream = None
        self._sos = None
        self._zi = None
        self._noise_floor = 0.0
        self._block_count = 0
        self._consecutive_over = 0
        self._active = False

        # Recording state — preroll is a bounded ring of raw (unfiltered)
        # blocks for the same preroll window the camera uses. _recording
        # samples accumulate when an incident is active. Both touched under
        # _rec_lock since the callback writes and the main thread reads.
        self._rec_lock = threading.Lock()
        self._preroll_blocks: deque = deque()
        self._preroll_max_blocks = 0
        self._recording_blocks: list = []
        self._recording = False
        # Logging state (callback thread only)
        self._last_logged_hot = False
        self._summary_start_ts = 0.0
        self._summary_blocks = 0
        self._summary_hot_blocks = 0
        self._summary_rms_sum = 0.0
        self._summary_peak_ratio = 0.0

    @property
    def audio_hot(self) -> bool:
        with self._lock:
            return self._audio_hot

    @property
    def last_hot_ts(self) -> float:
        with self._lock:
            return self._last_hot_ts

    def start(self) -> None:
        if not _AUDIO_AVAILABLE:
            logger.info(
                "Audio deps not installed (sounddevice, scipy). "
                "Run 'uv sync --extra audio' to enable. Audio monitoring disabled."
            )
            return
        if not self.config.audio_enabled:
            logger.info("Audio monitoring disabled in config.")
            return

        # Compute bandpass filter coefficients once
        nyquist = self.config.audio_sample_rate / 2.0
        low = self.config.audio_low_hz / nyquist
        high = self.config.audio_high_hz / nyquist
        self._sos = butter(
            self.config.audio_filter_order, [low, high], btype="band", output="sos"
        )

        # Initialize filter state for streaming (continuous across blocks)
        self._zi = sosfilt_zi(self._sos) * 0.0

        blocksize = int(self.config.audio_sample_rate * self.config.audio_block_seconds)

        # Size the preroll ring to match the visual preroll.
        self._preroll_max_blocks = max(
            1, int(self.config.preroll_seconds / self.config.audio_block_seconds)
        )
        self._preroll_blocks = deque(maxlen=self._preroll_max_blocks)

        device_index, device_name = _resolve_audio_device(self.config.audio_device_name)
        logger.info("audio device resolved: '%s' -> [%d] %s",
                    self.config.audio_device_name, device_index, device_name)

        try:
            self._stream = sd.InputStream(
                samplerate=self.config.audio_sample_rate,
                blocksize=blocksize,
                device=device_index,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
            self._active = True
            logger.info(
                "Audio monitoring started (device=[%d] %s, sr=%d, block=%d samples)",
                device_index, device_name,
                self.config.audio_sample_rate,
                blocksize,
            )
        except Exception:
            logger.exception(
                "Failed to open audio stream. Audio monitoring disabled."
            )
            self._active = False

    def _callback(self, indata, frames, time_info, status):
        """Sounddevice callback — runs on PortAudio thread."""
        if status:
            logger.debug("Audio callback status: %s", status)

        # indata shape: (blocksize, 1) float32 — squeeze to 1D.
        # Copy before stashing because PortAudio reuses the underlying buffer.
        block = indata[:, 0].copy()

        # Maintain raw preroll ring and active recording (under lock so the
        # main thread can snapshot consistently on incident end).
        with self._rec_lock:
            self._preroll_blocks.append(block)
            if self._recording:
                self._recording_blocks.append(block)

        # Apply bandpass filter with state carry-over
        filtered, self._zi = sosfilt(self._sos, block, zi=self._zi)

        # Compute RMS of filtered block
        rms = float(np.sqrt(np.mean(filtered**2)))

        self._block_count += 1

        # Update noise floor EMA
        if self._block_count == 1:
            self._noise_floor = rms
        else:
            alpha = self.config.audio_noise_floor_alpha
            self._noise_floor = alpha * rms + (1.0 - alpha) * self._noise_floor

        # During warm-up, don't trigger
        if self._block_count < self.config.audio_warmup_blocks:
            return

        # Determine hot/cold (guard against near-zero noise floor)
        floor = max(self._noise_floor, 1e-10)
        ratio = rms / floor
        over = ratio > self.config.audio_threshold
        # Asymmetric hysteresis: require N consecutive over-threshold blocks
        # to enter HOT; a single under-threshold block exits HOT immediately.
        # This filters transient pops while staying responsive when the jet
        # passes out of the band.
        if over:
            self._consecutive_over += 1
        else:
            self._consecutive_over = 0
        is_hot = self._consecutive_over >= self.config.audio_hot_consecutive_blocks

        now = time.time()
        with self._lock:
            self._audio_hot = is_hot
            if is_hot:
                self._last_hot_ts = now

        # Aggregate for periodic summary
        if self._summary_start_ts == 0.0:
            self._summary_start_ts = now
        self._summary_blocks += 1
        self._summary_rms_sum += rms
        if is_hot:
            self._summary_hot_blocks += 1
        if ratio > self._summary_peak_ratio:
            self._summary_peak_ratio = ratio

        # Log every hot/cold transition with the values that triggered it.
        if is_hot != self._last_logged_hot:
            logger.info(
                "audio %s rms=%.5f floor=%.5f ratio=%.2fx threshold=%.2fx",
                "HOT" if is_hot else "cold",
                rms, floor, ratio, self.config.audio_threshold,
            )
            self._last_logged_hot = is_hot

        # Periodic summary line.
        elapsed = now - self._summary_start_ts
        if elapsed >= self.config.audio_log_summary_seconds:
            mean_rms = self._summary_rms_sum / max(self._summary_blocks, 1)
            hot_frac = self._summary_hot_blocks / max(self._summary_blocks, 1)
            logger.info(
                "audio summary %.0fs: mean_rms=%.5f floor=%.5f peak_ratio=%.2fx hot=%.1f%%",
                elapsed, mean_rms, floor, self._summary_peak_ratio, hot_frac * 100.0,
            )
            self._summary_start_ts = now
            self._summary_blocks = 0
            self._summary_hot_blocks = 0
            self._summary_rms_sum = 0.0
            self._summary_peak_ratio = 0.0

    def start_incident_recording(self) -> None:
        """Begin accumulating raw samples; preroll ring is already populated."""
        if not self._active:
            return
        with self._rec_lock:
            self._recording_blocks = []
            self._recording = True

    def stop_incident_recording_and_save(self, wav_path: Path) -> None:
        """Stop accumulating and write preroll + recording to wav_path as int16 PCM.

        Safe to call even if recording was never started (writes whatever
        preroll exists). No-op if audio isn't active.
        """
        if not self._active:
            return
        with self._rec_lock:
            self._recording = False
            preroll = list(self._preroll_blocks)
            active = self._recording_blocks
            self._recording_blocks = []
        if not preroll and not active:
            return
        try:
            from scipy.io import wavfile
            samples = np.concatenate(preroll + active)
            # float32 [-1,1] -> int16 PCM for max player compatibility.
            samples_i16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
            wavfile.write(str(wav_path), self.config.audio_sample_rate, samples_i16)
            logger.info(
                "saved %s (%.1fs audio)",
                wav_path.name, len(samples_i16) / self.config.audio_sample_rate,
            )
        except Exception:
            logger.exception("failed to save incident audio to %s", wav_path)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("Error stopping audio stream")
            self._stream = None
            self._active = False
            logger.info("Audio monitoring stopped.")
