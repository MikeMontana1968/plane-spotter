# Prepare for field use

Checklist for leaving the laptop running unattended (e.g., in a car at the
office parking lot). All commands are PowerShell; run them in a fresh
window before deploying. Settings persist across reboots, so this is
one-time setup per machine.

## 1. Disable sleep / hibernate / display blanking

`-ac` = plugged in, `-dc` = on battery. `0` = never.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
```

## 2. Lid-close action -> do nothing

Default behavior is "sleep on lid close" and overrides every timeout above.
The killer if you close the lid to stash the laptop.

```powershell
powercfg -setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg -setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg -setactive SCHEME_CURRENT
```

Values: `0` = do nothing, `1` = sleep, `2` = hibernate, `3` = shutdown.

## 3. Disable USB selective suspend

Windows can power-cycle "idle" USB devices. The C920 between frames or
its mic between callbacks can look idle long enough to get suspended out
from under OpenCV / sounddevice.

```powershell
powercfg -setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg -setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg -setactive SCHEME_CURRENT
```

Same effect via GUI: Control Panel -> Power Options -> Change plan
settings -> Change advanced power settings -> USB settings -> USB selective
suspend setting -> **Disabled** for both On battery and Plugged in.

## 4. Verify

With `plane-spotter` running, this lists processes holding "stay awake"
requests. Python should appear once audio is streaming (the PortAudio
thread keeps the system awake on its own).

```powershell
powercfg /requests
```

## 5. Microphone permission

Settings -> Privacy & security -> Microphone:

- **Microphone access**: On
- **Let desktop apps access your microphone**: On

Desktop apps (Python included) are covered by the master toggle — no
per-app entry needed. To verify Python can actually read the C920 mic:

```powershell
uv run python tools/test_mic.py
```

Should print non-zero RMS values. If all zeros, the permission is blocked
or the wrong device is bound.

## Pre-deploy quick check

- [ ] All five powercfg sections above run
- [ ] `powercfg /requests` shows Python holding a request while running
- [ ] `tools/test_mic.py` reports non-zero RMS
- [ ] C920 mic enhancements disabled (Sound settings -> C920 -> Properties -> Enhancements off)
- [ ] Camera framing aimed at the flight corridor
- [ ] Power source confirmed (car USB-PD charger at 65W+, or accept battery runtime)
- [ ] Plenty of disk free for incident JPEGs + per-incident audio.wav
