#!/usr/bin/env python3
"""
FOMO Roller — low-battery clean shutdown guard
==============================================
Polls the PiSugar battery via pisugar-server and, when it runs low while NOT
charging, triggers a clean `shutdown` so the SD card unmounts safely — instead
of the battery hard-cutting power, which risks filesystem/Wi-Fi-profile
corruption with no one on the playa to fix it.

Talks to pisugar-server's command socket (TCP 127.0.0.1:8423), same as
rtc_sync.py. Runs as root (needs to call shutdown) via its systemd unit.

Two independent triggers, either one fires (guards against a drifting % gauge):
percentage <= FOMO_BATT_MIN, or resting voltage <= FOMO_BATT_VMIN. A single
low read won't act — it must persist for FOMO_BATT_CONFIRM consecutive polls,
so a momentary sag under load (or a bad read) can't cut the party short.

Tunables (env, override in the unit if needed):
    FOMO_BATT_MIN      percent threshold                     (default 15)
    FOMO_BATT_VMIN     volt threshold                        (default 3.40)
    FOMO_BATT_POLL     seconds between reads                 (default 30)
    FOMO_BATT_CONFIRM  consecutive low reads before shutdown (default 3)
"""

import os
import socket
import subprocess
import sys
import time

HOST, PORT = "127.0.0.1", 8423
PCT_MIN  = float(os.environ.get("FOMO_BATT_MIN", "15"))
VOLT_MIN = float(os.environ.get("FOMO_BATT_VMIN", "3.40"))
POLL     = float(os.environ.get("FOMO_BATT_POLL", "30"))
CONFIRM  = int(os.environ.get("FOMO_BATT_CONFIRM", "3"))


def query(cmd):
    """Send one command to pisugar-server, return its reply text."""
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.sendall((cmd + "\n").encode())
        time.sleep(0.2)
        return s.recv(1024).decode(errors="replace").strip()


def value(cmd):
    """Parse the numeric tail of a reply like 'battery: 92.6' -> '92.6'."""
    return query(cmd).split(":", 1)[1].strip()


def main():
    print(f"battery guard: clean shutdown below {PCT_MIN:.0f}% or {VOLT_MIN:.2f}V "
          f"when not charging, confirmed over {CONFIRM} reads @ {POLL:.0f}s",
          flush=True)
    low = 0
    while True:
        try:
            pct      = float(value("get battery"))
            volts    = float(value("get battery_v"))
            charging = "true" in query("get battery_charging").lower()
        except (OSError, ValueError, IndexError) as e:
            # A read error is not evidence of low battery — never act on it.
            print(f"battery read failed: {e}", flush=True)
            time.sleep(POLL)
            continue

        if charging or (pct > PCT_MIN and volts > VOLT_MIN):
            if low:
                print(f"battery recovered: {pct:.0f}% {volts:.2f}V "
                      f"charging={charging}", flush=True)
            low = 0
        else:
            low += 1
            print(f"LOW battery {pct:.0f}% {volts:.2f}V not charging "
                  f"({low}/{CONFIRM})", flush=True)
            if low >= CONFIRM:
                print("Triggering clean shutdown", flush=True)
                subprocess.run(["/sbin/shutdown", "-h", "now",
                                "FOMO Roller: PiSugar battery low"])
                return 0
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
