"""Verify the configured audio device opens and produces non-silent samples.

Reads ~2 seconds of audio from the device chosen by config.audio_device_name
and prints per-block RMS. If every value is 0.000000, mic permission is
denied or the wrong device is bound. Non-zero values mean the stream is
live; speak/clap during the test to see the numbers spike.
"""
from __future__ import annotations

import time

import numpy as np
import sounddevice as sd

from plane_spotter.audio import _resolve_audio_device
from plane_spotter.config import Config

cfg = Config()
idx, name = _resolve_audio_device(cfg.audio_device_name)
print(f"resolved '{cfg.audio_device_name}' -> [{idx}] {name}")
print(f"opening at {cfg.audio_sample_rate} Hz, "
      f"{cfg.audio_block_seconds*1000:.0f} ms blocks...")

blocks = []
def cb(indata, frames, time_info, status):
    if status:
        print(f"  status: {status}")
    blocks.append(indata[:, 0].copy())

blocksize = int(cfg.audio_sample_rate * cfg.audio_block_seconds)
with sd.InputStream(
    samplerate=cfg.audio_sample_rate,
    blocksize=blocksize,
    device=idx,
    channels=1,
    dtype="float32",
    callback=cb,
):
    time.sleep(2.0)

print(f"\ncaptured {len(blocks)} blocks. Per-block RMS:")
for i, b in enumerate(blocks):
    rms = float(np.sqrt(np.mean(b**2)))
    bar = "#" * min(60, int(rms * 5000))
    print(f"  block {i:2d}: rms={rms:.6f}  {bar}")

all_zero = all(np.all(b == 0) for b in blocks)
if all_zero:
    print("\nFAIL: all samples were exactly zero.")
    print("  Likely cause: mic permission denied for desktop apps.")
    print("  Fix: Settings -> Privacy & security -> Microphone ->")
    print("       'Let desktop apps access your microphone' = On")
else:
    nonzero_rms = [float(np.sqrt(np.mean(b**2))) for b in blocks]
    print(f"\nOK: non-zero audio captured. "
          f"mean RMS={np.mean(nonzero_rms):.6f}, "
          f"max RMS={max(nonzero_rms):.6f}")
