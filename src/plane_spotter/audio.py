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

import numpy as np

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    from scipy.signal import butter, sosfilt, sosfilt_zi

    _AUDIO_AVAILABLE = True
except ImportError:
    _AUDIO_AVAILABLE = False


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
        self._active = False
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

        try:
            self._stream = sd.InputStream(
                samplerate=self.config.audio_sample_rate,
                blocksize=blocksize,
                device=self.config.audio_device,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
            self._active = True
            logger.info(
                "Audio monitoring started (device=%s, sr=%d, block=%d samples)",
                self.config.audio_device,
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

        # indata shape: (blocksize, 1) float32 — squeeze to 1D
        block = indata[:, 0]

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
        is_hot = ratio > self.config.audio_threshold

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
