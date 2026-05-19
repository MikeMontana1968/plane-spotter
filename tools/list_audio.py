"""List sounddevice input devices so we can pick the camera's mic."""
import sounddevice as sd

for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        hostapi = sd.query_hostapis(d["hostapi"])["name"]
        print(f"[{i:3d}] in={d['max_input_channels']} "
              f"sr={int(d['default_samplerate'])} "
              f"hostapi={hostapi:12s} {d['name']}")
