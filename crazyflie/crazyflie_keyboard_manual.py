#!/usr/bin/env python3
"""
Keyboard control for a Crazyflie 2.1 -- same control style as the phone app
(tilt / turn / thrust). NO Flow deck needed. The drone self-levels but does NOT
hold height or position, so you fly it like the app's joysticks.

Run:   python crazyflie_keyboard_manual.py
Check: python crazyflie_keyboard_manual.py --check     (connect + battery only)

Keys:
  UP / DOWN     thrust up / down (hold to change, it STAYS where you leave it)
  W / S         tilt forward / backward (while held)
  A / D         tilt left / right (while held)
  LEFT / RIGHT  turn left / right (while held)
  X             thrust to zero (motors idle, still connected)
  Q             ramp thrust down and quit
  SPACE         EMERGENCY STOP (motors off immediately)

Every command is logged to outputs/crazyflie/keyboard_manual_commands_<K>.csv
Start on a soft floor, tethered if possible, clear 3 m around. Ctrl+C also stops motors.
"""
import argparse
import csv
import os
import sys
import threading
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
from cflib.crazyflie.log import LogConfig
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from pynput import keyboard

# ---- limits (conservative) ---------------------------------------------------
HARD_MAX_THRUST = 50000    # never exceed, whatever you do
MAX_TILT = 8.0             # degrees while a tilt key is held (raise slowly)
YAW_RATE = 40.0            # deg/s while a turn key is held
THRUST_RATE = 15000.0      # thrust units per second while UP/DOWN held
MIN_BATTERY_V = 3.6
DT = 0.02                  # 50 Hz command stream; the drone cuts out if commands stop


class Keys:
    def __init__(self):
        self.lock = threading.Lock()
        self.down, self.quit, self.estop, self.zero = set(), False, False, False

    @staticmethod
    def _name(k):
        if isinstance(k, keyboard.KeyCode) and k.char:
            return k.char.lower()
        return {keyboard.Key.up: "up", keyboard.Key.down: "down",
                keyboard.Key.left: "left", keyboard.Key.right: "right",
                keyboard.Key.space: "space"}.get(k)

    def press(self, k):
        n = self._name(k)
        with self.lock:
            if n == "q":
                self.quit = True
            elif n == "space":
                self.estop = True
            elif n == "x":
                self.zero = True
            elif n:
                self.down.add(n)

    def release(self, k):
        with self.lock:
            self.down.discard(self._name(k))

    def snap(self):
        with self.lock:
            z = self.zero
            self.zero = False
            return set(self.down), self.quit, self.estop, z


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--max-tilt", type=float, default=MAX_TILT)
    ap.add_argument("--flip-pitch", action="store_true", help="use if W/S move the wrong way")
    ap.add_argument("--flip-roll", action="store_true", help="use if A/D move the wrong way")
    args = ap.parse_args()
    tilt = min(args.max_tilt, 15.0)
    psign = -1 if args.flip_pitch else 1
    rsign = -1 if args.flip_roll else 1

    cflib.crtp.init_drivers()
    uri = args.uri
    if not uri:
        found = cflib.crtp.scan_interfaces()
        if not found:
            sys.exit("No Crazyflie found (is it on, radio plugged in, phone app closed?).")
        uri = found[0][0]

    log_rows, t0, vbat = [], time.time(), {"v": 0.0}
    with SyncCrazyflie(uri) as scf:
        cf = scf.cf
        lost = {"v": False}
        cf.disconnected.add_callback(lambda _u: lost.__setitem__("v", True))
        cf.connection_lost.add_callback(lambda _u, _m: lost.__setitem__("v", True))

        pm = LogConfig(name="pm", period_in_ms=200)
        pm.add_variable("pm.vbat", "float")
        cf.log.add_config(pm)
        pm.data_received_cb.add_callback(lambda _t, d, _l: vbat.__setitem__("v", d["pm.vbat"]))
        pm.start()
        time.sleep(1.2)
        print(f"Connected. Battery {vbat['v']:.2f} V")
        # Clear a crash/tumble lockout and arm, so no power cycle is needed after a crash.
        # (Only works on firmware that supports it; harmless otherwise.)
        try:
            cf.platform.send_crash_recovery_request()
            time.sleep(0.2)
            cf.platform.send_arming_request(True)
            time.sleep(0.2)
            print("Sent crash-recovery and arm requests.")
        except Exception as ex:
            print("Recovery/arm request not supported on this firmware:", ex)
        if args.check:
            pm.stop()
            return
        if vbat["v"] < MIN_BATTERY_V:
            sys.exit("Battery too low, charge it first.")

        keys = Keys()
        listener = keyboard.Listener(on_press=keys.press, on_release=keys.release)
        listener.start()
        print("Click this window so it gets your keys.  UP/DOWN = thrust, W/A/S/D = tilt, "
              "arrows L/R = turn, Q = land/quit, SPACE = EMERGENCY STOP.")
        print("Starting in 3 s...")
        time.sleep(3)

        thrust = 0.0
        try:
            cf.commander.send_setpoint(0, 0, 0, 0)   # unlock
            time.sleep(0.1)
            while not lost["v"]:
                down, q, e, z = keys.snap()
                if e:
                    raise KeyboardInterrupt
                if q:
                    break
                if z:
                    thrust = 0.0
                thrust += THRUST_RATE * DT * (("up" in down) - ("down" in down))
                thrust = max(0.0, min(HARD_MAX_THRUST, thrust))
                pitch = psign * tilt * (("w" in down) - ("s" in down))
                roll = rsign * tilt * (("d" in down) - ("a" in down))
                yaw = YAW_RATE * (("right" in down) - ("left" in down))
                if vbat["v"] and vbat["v"] < 3.3:
                    print("Battery critical, landing.")
                    break
                cf.commander.send_setpoint(roll, pitch, yaw, int(thrust))
                log_rows.append([round(time.time() - t0, 3), roll, pitch, yaw, int(thrust),
                                 round(vbat["v"], 2)])
                time.sleep(DT)
            # gentle ramp-down on quit / low battery
            while thrust > 0 and not lost["v"]:
                thrust = max(0.0, thrust - 12000 * DT)
                cf.commander.send_setpoint(0, 0, 0, int(thrust))
                time.sleep(DT)
        except KeyboardInterrupt:
            print("EMERGENCY STOP")
        finally:
            cf.commander.send_stop_setpoint()
            listener.stop()
            pm.stop()
            print("Motors off.")

    os.makedirs("outputs/crazyflie", exist_ok=True)
    k = 1
    while os.path.exists(f"outputs/crazyflie/keyboard_manual_commands_{k}.csv"):
        k += 1
    path = f"outputs/crazyflie/keyboard_manual_commands_{k}.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "roll_deg", "pitch_deg", "yaw_rate_dps", "thrust", "battery_v"])
        w.writerows(log_rows)
    print(f"Saved {len(log_rows)} command rows to {path}")


if __name__ == "__main__":
    main()
