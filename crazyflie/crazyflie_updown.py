#!/usr/bin/env python3
"""
Simple up/down test for a Crazyflie 2.1 WITHOUT a Flow deck (raw thrust, no position hold).

The drone self-levels but WILL drift sideways and the height depends on --thrust.
Start low and raise --thrust a little each run until it lifts gently:

    python crazyflie_updown.py --thrust 25000     (motors spin, stays on floor)
    python crazyflie_updown.py --thrust 33000
    python crazyflie_updown.py --thrust 37000     (...raise by ~2000 each run)

Stop as soon as it lifts. Ctrl+C cuts the motors immediately.
Tether it (light thread to the floor) or fly over a soft surface. Clear 3 m around it.
"""
import argparse
import sys
import time

# Windows fix: make pyusb find the libusb DLL that ships with libusb-package.
try:
    import libusb_package
    import usb.backend.libusb1 as _libusb1
    _orig_get_backend = _libusb1.get_backend
    _libusb1.get_backend = lambda find_library=None: _orig_get_backend(
        find_library=libusb_package.find_library)
except Exception:
    pass

import cflib.crtp
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie

HARD_MAX_THRUST = 48000   # never exceed this, whatever --thrust says
DT = 0.02                 # 50 Hz command stream (drone cuts out if commands stop)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", help="radio URI (auto-detected if omitted)")
    ap.add_argument("--thrust", type=int, default=25000, help="peak thrust, 0-65535 (default 25000)")
    ap.add_argument("--up", type=float, default=3.0, help="seconds at peak thrust")
    ap.add_argument("--ramp", type=float, default=1.5, help="seconds to ramp up and to ramp down")
    args = ap.parse_args()

    thrust = min(args.thrust, HARD_MAX_THRUST)
    if thrust != args.thrust:
        print(f"Thrust capped at {HARD_MAX_THRUST}")

    cflib.crtp.init_drivers()
    uri = args.uri
    if not uri:
        found = cflib.crtp.scan_interfaces()
        if not found:
            sys.exit("No Crazyflie found.")
        uri = found[0][0]

    with SyncCrazyflie(uri) as scf:
        cf = scf.cf
        print(f"Connected. Peak thrust {thrust}, hold {args.up}s. Starting in 3 s, Ctrl+C to stop.")
        time.sleep(3)
        try:
            cf.commander.send_setpoint(0, 0, 0, 0)  # unlock
            time.sleep(0.1)

            t0 = time.time()
            total = args.ramp + args.up + args.ramp
            while True:
                t = time.time() - t0
                if t >= total:
                    break
                if t < args.ramp:                       # ramp up
                    th = thrust * (t / args.ramp)
                elif t < args.ramp + args.up:           # hold
                    th = thrust
                else:                                   # ramp down
                    th = thrust * (1 - (t - args.ramp - args.up) / args.ramp)
                cf.commander.send_setpoint(0, 0, 0, int(max(0, th)))
                time.sleep(DT)
        finally:
            cf.commander.send_stop_setpoint()   # motors off
            print("Done, motors off.")


if __name__ == "__main__":
    main()
