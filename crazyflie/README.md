# Crazyflie keyboard control (EGR-005)

Keyboard control for a Crazyflie 2.1 over a Crazyradio, replacing the phone app.
Part of the capstone project on behavioral signatures of human vs. autonomous UAV pilots.

## Scripts

| Script | What it does | Needs |
|---|---|---|
| `crazyflie_keyboard_manual.py` | App-style control: thrust, tilt, turn from the keyboard. Logs every command to CSV. | Crazyflie + Crazyradio |
| `crazyflie_keyboard.py` | Hover-mode control (hold-to-move, auto-hover). Logs position/velocity in the same CSV format as the Gazebo sim. **Untested on hardware.** | Flow deck v2 |
| `crazyflie_updown.py` | Simple thrust ramp test (up for N seconds, then down). | Crazyflie + Crazyradio |

All scripts include a Windows fix so pyusb finds the libusb DLL from `libusb-package`.

## Setup (Windows, PowerShell)

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Crazyradio 2.0 on Windows:
- If the radio's LED pulses red and shows up as a "Crazyradio2" USB drive, it is in bootloader mode: unplug and replug without touching the button (a white flash on startup means normal mode).
- Install the USB driver with Zadig (Options > List All Devices > Crazyradio 2.0 > WinUSB > Replace Driver).
- If the radio errors with "device not functioning", unplug, restart, and replug.

## Run

```
python crazyflie_keyboard_manual.py --check     # connect + battery only, no flying
python crazyflie_keyboard_manual.py             # fly
```

Controls (`crazyflie_keyboard_manual.py`):

| Key | Action |
|---|---|
| Up / Down | thrust up / down (stays where you leave it) |
| W / S | tilt forward / backward (while held) |
| A / D | tilt left / right (while held) |
| Left / Right | turn |
| X | thrust to zero |
| Q | ramp down and quit |
| Space | **emergency stop** (motors off) |

If W/S or A/D move the wrong way, rerun with `--flip-pitch` or `--flip-roll`.
Use `--max-tilt 5` for gentler tilt. Commands are logged to `outputs/crazyflie/`.

## Safety
- Fly over a soft surface with 3 m clear around the drone, tethered if possible.
- Check props for damage after any crash; never fly with a bent prop.
- Do not leave a LiPo charging unattended.
- Ctrl+C or Space cuts the motors.

## Status / known limits
- Without a Flow deck the drone self-levels but cannot hold height or position, so manual flight is hard and position data is not usable.
- A Flow deck v2 is needed for hover mode and for logging position data comparable to the Gazebo sim.
- Not yet tested on hardware: `crazyflie_keyboard.py` (hover mode), the crash-recovery request.
- Sim-to-real replay (replaying a Gazebo CSV on the drone) is planned once a Flow deck is available.
