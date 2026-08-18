#!/usr/bin/env python3
"""
FOMO Roller — RTC sync helper
=============================
Talks to pisugar-server to sync the PiSugar 3's onboard RTC and the Linux
system clock. pisugar-server does the privileged work (reading the RTC over
I2C and calling settimeofday); this just sends it the command.

Why this exists: pisugar-server does NOT set the system clock from the RTC on
its own at boot, and there's no network on the playa to fall back on NTP. A
systemd unit runs this at boot with 'rtc_rtc2pi' so every power-up gets the
right time from the battery-backed RTC before playback picks its MP3 slot.

Usage:
    rtc_sync.py                # rtc_rtc2pi  (RTC -> system; the boot case)
    rtc_sync.py rtc_pi2rtc     # system -> RTC (seed the RTC while online)
    rtc_sync.py rtc_web        # internet -> RTC + system (if online)

pisugar-server command socket is TCP 127.0.0.1:8423 (see its docs).
"""

import socket
import sys
import time

HOST, PORT = "127.0.0.1", 8423
ALLOWED = {"rtc_rtc2pi", "rtc_pi2rtc", "rtc_web"}
RETRIES = 15            # pisugar-server may not be up yet at boot
RETRY_SLEEP = 2.0


def send(cmd):
    """Send one command to pisugar-server, return its reply text."""
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.sendall((cmd + "\n").encode())
        time.sleep(0.2)
        return s.recv(1024).decode(errors="replace").strip()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rtc_rtc2pi"
    if cmd not in ALLOWED:
        print(f"unknown command {cmd!r}; use one of {sorted(ALLOWED)}")
        return 2

    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            reply = send(cmd)
            print(f"{cmd}: {reply or 'ok'}")
            return 0
        except OSError as e:               # server socket not ready yet
            last_err = e
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP)
    print(f"{cmd}: failed to reach pisugar-server after {RETRIES} tries: {last_err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
