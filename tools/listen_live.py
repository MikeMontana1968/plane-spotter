"""Quick-look: capture N seconds from the configured mic and show what the
plane-spotter bandpass+threshold logic sees, in real time.

Use this when you know there's an aircraft passing right now to gut-check
whether the audio path detects it.
"""
from __future__ import annotations

import sys
import time

import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt, sosfilt_zi

from plane_spotter.audio import _resolve_audio_device
from plane_spotter.config import Config

DURATION_S = int(sys.argv[1]) if len(sys.argv) > 1 else 30

cfg = Config()
idx, name = _resolve_audio_device(cfg.audio_device_name)
print(f"listening on [{idx}] {name} for {DURATION_S}s "
      f"(band {cfg.audio_low_hz}-{cfg.audio_high_hz} Hz, "
      f"threshold {cfg.audio_threshold}x)\n")

nyquist = cfg.audio_sample_rate / 2.0
sos = butter(cfg.audio_filter_order,
             [cfg.audio_low_hz / nyquist, cfg.audio_high_hz / nyquist],
             btype="band", output="sos")
zi = sosfilt_zi(sos) * 0.0

blocksize = int(cfg.audio_sample_rate * cfg.audio_block_seconds)
floor = 0.0
alpha = cfg.audio_noise_floor_alpha
n = 0
peak_ratio = 0.0
peak_rms = 0.0
consec_over = 0
hot = False
start = time.time()
rows = []

def cb(indata, frames, time_info, status):
    global floor, n, peak_ratio, peak_rms, consec_over, hot, zi
    block = indata[:, 0].copy()
    filtered, zi = sosfilt(sos, block, zi=zi)
    rms = float(np.sqrt(np.mean(filtered**2)))
    n += 1
    if n == 1:
        floor = rms
    else:
        floor = alpha * rms + (1 - alpha) * floor
    f = max(floor, 1e-10)
    ratio = rms / f
    over = ratio > cfg.audio_threshold
    consec_over = consec_over + 1 if over else 0
    was_hot = hot
    hot = consec_over >= cfg.audio_hot_consecutive_blocks
    if ratio > peak_ratio:
        peak_ratio = ratio
        peak_rms = rms
    rows.append((time.time() - start, rms, floor, ratio, hot))
    # live tick every ~1s
    if n % 10 == 0 or hot != was_hot:
        bar = "#" * min(50, int(ratio * 2))
        flag = " HOT" if hot else ""
        print(f"  t={rows[-1][0]:5.1f}s  rms={rms:.5f}  floor={floor:.5f}  "
              f"ratio={ratio:5.2f}x  {bar}{flag}")

with sd.InputStream(samplerate=cfg.audio_sample_rate, blocksize=blocksize,
                    device=idx, channels=1, dtype="float32", callback=cb):
    time.sleep(DURATION_S)

print(f"\n--- summary over {DURATION_S}s ---")
print(f"peak ratio: {peak_ratio:.2f}x  (at rms={peak_rms:.5f})")
ratios = [r[3] for r in rows]
ratios_sorted = sorted(ratios)
p50 = ratios_sorted[len(ratios_sorted)//2]
p90 = ratios_sorted[int(len(ratios_sorted)*0.9)]
p99 = ratios_sorted[int(len(ratios_sorted)*0.99)]
hot_frac = sum(1 for r in rows if r[4]) / len(rows)
print(f"ratio p50={p50:.2f}x  p90={p90:.2f}x  p99={p99:.2f}x")
print(f"would have been HOT for {hot_frac*100:.1f}% of blocks "
      f"(threshold {cfg.audio_threshold}x, "
      f"{cfg.audio_hot_consecutive_blocks} consec)")
