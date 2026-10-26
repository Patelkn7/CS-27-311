#!/usr/bin/env python3
"""
Keyboard flight for a Crazyflie 2.1 with a Flow deck.
Logs the same 7-column CSV as keyboard_drone.py (NED, down negative = higher).

Setup:   pip install cflib pynput
Check:   python3 crazyflie_keyboard.py --check      (no flying: radio, deck, battery)
Fly:     python3 crazyflie_keyboard.py

Keys (hold to move, release to hover):
  W / S        forward / backward
  A / D        left / right
  UP / DOWN    climb / descend
  LEFT / RIGHT yaw left / right
  Q            land and quit
  SPACE        EMERGENCY STOP (cuts motors, drone drops)

macOS: the terminal app needs Input Monitoring + Accessibility permission
(System Settings > Privacy & Security) or pynput sees no keys.
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

# ---- tuning (start gentle) -------------------------------------------------
SPEED = 0.3          # m/s horizontal
YAW_RATE = 45.0      # deg/s
CLIMB_RATE = 0.2     # m/s change of target height
TAKEOFF_HEIGHT = 0.4  # m
MIN_HEIGHT = 0.2
MAX_HEIGHT = 1.2
MIN_BATTERY_V = 3.8  # refuse to fly below this
LOOP_HZ = 20


class Keys:
    def __init__(self):
        self.lock = threading.Lock()
        self.down = set()
        self.quit = False
        self.estop = False

    def press(self, k):
        with self.lock:
            name = self._name(k)
            if name == "q":
                self.quit = True
            elif name == "space":
                self.estop = True
            elif name:
                self.down.add(name)

    def release(self, k):
        with self.lock:
            self.down.discard(self._name(k))

    @staticmethod
    def _name(k):
        if isinstance(k, keyboard.KeyCode) and k.char:
            return k.char.lower()
        return {keyboard.Key.up: "up", keyboard.Key.down: "down",
                keyboard.Key.left: "left", keyboard.Key.right: "right",
                keyboard.Key.space: "space"}.get(k)

    def snapshot(self):
        with self.lock:
            return set(self.down), self.quit, self.estop


def find_uri(arg_uri):
    if arg_uri:
        return arg_uri
    cflib.crtp.init_drivers()
    found = cflib.crtp.scan_interfaces()
    if not found:
        sys.exit("No Crazyflie found. Is it on, the radio plugged in, and nothing else (the phone app / "
                 "cfclient) connected to it?")
    print("Found:", found)
    return found[0][0]


def next_csv_path():
    os.makedirs("outputs/crazyflie", exist_ok=True)
    k = 1
    while os.path.exists(f"outputs/crazyflie/human_crazyflie_telemetry_data_{k}.csv"):
        k += 1
    return f"outputs/crazyflie/human_crazyflie_telemetry_data_{k}.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", help="e.g. radio://0/80/2M/E7E7E7E7E7 (auto-detected if omitted)")
    ap.add_argument("--check", action="store_true", help="connect and report only, do not fly")
    args = ap.parse_args()

    cflib.crtp.init_drivers()
    uri = find_uri(args.uri)
    rows = []
    t0 = time.time()
    latest = {"vbat": 0.0}

    with SyncCrazyflie(uri) as scf:
        cf = scf.cf
        lost = {"v": False}
        cf.disconnected.add_callback(lambda _u: lost.__setitem__("v", True))
        cf.connection_lost.add_callback(lambda _u, _m: lost.__setitem__("v", True))

        # ---- pre-flight checks -------------------------------------------
        deck = cf.param.get_value("deck.bcFlow2")
        deck1 = cf.param.get_value("deck.bcFlow")
        has_flow = str(deck) == "1" or str(deck1) == "1"
        print("Flow deck detected:", has_flow)

        pm = LogConfig(name="pm", period_in_ms=200)
        pm.add_variable("pm.vbat", "float")
        cf.log.add_config(pm)
        pm.data_received_cb.add_callback(lambda _t, d, _l: latest.__setitem__("vbat", d["pm.vbat"]))
        pm.start()

        st = LogConfig(name="state", period_in_ms=30)
        for v in ("stateEstimate.x", "stateEstimate.y", "stateEstimate.z",
                  "stateEstimate.vx", "stateEstimate.vy", "stateEstimate.vz"):
            st.add_variable(v, "float")
        cf.log.add_config(st)

        def on_state(_ts, d, _l):
            # Crazyflie frame: x forward, y left, z up  ->  NED: north=x, east=-y, down=-z
            rows.append([round(time.time() - t0, 3),
                         d["stateEstimate.x"], -d["stateEstimate.y"], -d["stateEstimate.z"],
                         d["stateEstimate.vx"], -d["stateEstimate.vy"], -d["stateEstimate.vz"]])
        st.data_received_cb.add_callback(on_state)
        st.start()
        time.sleep(1.5)
        print(f"Battery: {latest['vbat']:.2f} V")

        if args.check:
            print("Check mode: not flying. Lift/move the drone by hand; position values should change.")
            time.sleep(3)
            if rows:
                print("Last position (N,E,D):", [round(x, 2) for x in rows[-1][1:4]])
            return
        if not has_flow:
            sys.exit("No Flow deck detected. Refusing to fly: the drone cannot hold position without it.")
        if latest["vbat"] < MIN_BATTERY_V:
            sys.exit(f"Battery too low ({latest['vbat']:.2f} V). Charge it first.")

        # ---- keyboard ----------------------------------------------------
        keys = Keys()
        listener = keyboard.Listener(on_press=keys.press, on_release=keys.release)
        listener.start()

        print("\nCLEAR THE AREA. Taking off in 3 s.  Q = land, SPACE = emergency stop.")
        time.sleep(3)

        dt = 1.0 / LOOP_HZ
        cf.commander.send_setpoint(0, 0, 0, 0)  # unlock thrust protection
        time.sleep(0.1)

        height = 0.0
        try:
            # takeoff ramp
            while height < TAKEOFF_HEIGHT and not lost["v"]:
                _, q, e = keys.snapshot()
                if e:
                    raise KeyboardInterrupt
                height = min(TAKEOFF_HEIGHT, height + 0.25 * dt * 2)
                cf.commander.send_hover_setpoint(0, 0, 0, height)
                time.sleep(dt)

            print("Flying.")
            while not lost["v"]:
                down, q, e = keys.snapshot()
                if e:
                    raise KeyboardInterrupt
                if q:
                    break
                vx = SPEED * (("w" in down) - ("s" in down))
                vy = SPEED * (("a" in down) - ("d" in down))       # +y = left
                yaw = YAW_RATE * (("left" in down) - ("right" in down))  # + = counter-clockwise
                height += CLIMB_RATE * dt * (("up" in down) - ("down" in down))
                height = max(MIN_HEIGHT, min(MAX_HEIGHT, height))
                if latest["vbat"] and latest["vbat"] < 3.3:
                    print("Battery critical, landing.")
                    break
                cf.commander.send_hover_setpoint(vx, vy, yaw, height)
                time.sleep(dt)
        except KeyboardInterrupt:
            print("EMERGENCY STOP")
            cf.commander.send_stop_setpoint()
            height = None
        finally:
            if height is not None and not lost["v"]:
                print("Landing...")
                while height > 0.05:
                    height -= 0.2 * dt * 2
                    cf.commander.send_hover_setpoint(0, 0, 0, max(height, 0.03))
                    time.sleep(dt)
                cf.commander.send_stop_setpoint()
            listener.stop()
            st.stop()
            pm.stop()

    path = next_csv_path()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "north_m", "east_m", "down_m",
                    "v_north_m_s", "v_east_m_s", "v_down_m_s"])
        w.writerows(rows)
    print(f"Saved {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
