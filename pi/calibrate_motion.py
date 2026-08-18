#!/usr/bin/env python3
"""
FOMO Roller — Motion Calibration Tool
=====================================
One-off tool to figure out how to tell *rolling* apart from just being
moved/picked up/bumped, so playback only triggers on an actual roll.

It logs all six MPU-6050 axes (accelerometer + gyroscope) at 20Hz to a CSV,
while walking you through a short timed routine. Each phase is auto-labeled
in the CSV so the data is easy to analyze afterward.

Usage on the Pi:
    sudo systemctl stop fomo-roller        # free the I2C bus first
    python3 calibrate_motion.py            # follow the on-screen prompts

Then copy the CSV back to the Mac to analyze:
    rsync pi@fomoroller.local:/home/pi/roll_calib.csv .

The physical idea: rolling is sustained rotation about the roller's long
axis, so one gyro axis should light up during 'roll' and stay quiet during
'pickup_walk' / 'bump'. The accelerometer alone can't tell those apart.
"""

import smbus2
import time
import csv
import os

MPU_ADDR = 0x69          # 0x69, not 0x68 — 0x68 is the PiSugar 3's RTC
OUT_PATH = os.path.expanduser("~/roll_calib.csv")   # writes to your home dir
SAMPLE_HZ = 20
PAUSE_SEC = 6            # un-recorded "get ready" gap before each phase, so
                        # you can stop one motion and start the next cleanly

# MPU-6050 sensitivity at default full-scale ranges
ACCEL_LSB_PER_G   = 16384.0     # ±2 g
GYRO_LSB_PER_DPS  = 131.0       # ±250 °/s

# Timed routine: (seconds, label, instruction). Do exactly what each says.
# Each phase is preceded by an un-recorded PAUSE_SEC countdown (see below).
PHASES = [
    (15, "still",         "Set it down and DON'T touch it"),
    (25, "roll",          "ROLL it back and forth like you're using it"),
    (20, "pickup_walk",   "Pick it up and carry it around / reposition it"),
    (18, "bump",          "Nudge, tap, and bump it WITHOUT rolling"),
    (12, "setdown_still", "Set it down and leave it still again"),
]


def read_all(bus):
    """Block-read accel(6) + temp(2) + gyro(6) = 14 bytes from 0x3B."""
    d = bus.read_i2c_block_data(MPU_ADDR, 0x3B, 14)

    def s16(hi, lo):
        v = (hi << 8) | lo
        return v - 65536 if v >= 32768 else v

    ax, ay, az = s16(d[0], d[1]), s16(d[2], d[3]), s16(d[4], d[5])
    gx, gy, gz = s16(d[8], d[9]), s16(d[10], d[11]), s16(d[12], d[13])
    return ax, ay, az, gx, gy, gz


def main():
    bus = smbus2.SMBus(1)
    bus.write_byte_data(MPU_ADDR, 0x6B, 0)      # wake the sensor
    time.sleep(0.1)
    # Enable the on-chip digital low-pass filter (~44Hz) to cut real high-
    # frequency noise, and pin the full-scale ranges we assume below.
    bus.write_byte_data(MPU_ADDR, 0x1A, 0x03)   # DLPF_CFG=3
    bus.write_byte_data(MPU_ADDR, 0x1B, 0x00)   # gyro ±250 °/s
    bus.write_byte_data(MPU_ADDR, 0x1C, 0x00)   # accel ±2 g
    time.sleep(0.1)

    total = sum(p[0] for p in PHASES)
    print(f"Logging {total}s of data (plus {PAUSE_SEC}s pauses) to {OUT_PATH}")
    print("Each phase starts only after a countdown, so you have time to set up.\n")

    dt = 1.0 / SAMPLE_HZ
    with open(OUT_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "ax", "ay", "az", "gx", "gy", "gz", "label", "bad"])

        t0 = time.time()
        bad_count = 0
        for duration, label, instruction in PHASES:
            # Un-recorded "get ready" countdown so you can switch motions
            # cleanly between phases (no data logged during this gap).
            print(f"\n--- NEXT: {label.upper()} — {instruction}")
            for s in range(PAUSE_SEC, 0, -1):
                print(f"    get ready... starting in {s}s  ", end="\r")
                time.sleep(1)
            print(f"\n>>> RECORDING {label.upper()} ({duration}s): {instruction}")
            phase_end = time.time() + duration
            while time.time() < phase_end:
                t = time.time() - t0
                ax, ay, az, gx, gy, gz = read_all(bus)
                # Flag torn/corrupt I2C reads: a value at/near full-scale
                # (±32767) is almost certainly a corrupted transaction, not
                # real motion. We keep them in the log but mark them so the
                # analysis can reject them and measure the corruption rate.
                bad = 1 if any(abs(v) >= 32000 for v in (ax, ay, az, gx, gy, gz)) else 0
                bad_count += bad
                w.writerow([f"{t:.2f}", ax, ay, az, gx, gy, gz, label, bad])
                # live gyro readout in deg/s so you can eyeball the roll axis
                print(f"  t={t:5.1f}  gyro(°/s) "
                      f"x={gx / GYRO_LSB_PER_DPS:7.1f} "
                      f"y={gy / GYRO_LSB_PER_DPS:7.1f} "
                      f"z={gz / GYRO_LSB_PER_DPS:7.1f}", end="\r")
                time.sleep(dt)

    print(f"\n\nDone. Saved {OUT_PATH}")
    total_samples = sum(1 for _ in open(OUT_PATH)) - 1
    print(f"Corrupt (torn) reads flagged: {bad_count}/{total_samples} "
          f"({100 * bad_count / max(1, total_samples):.1f}%)")
    print(f"Copy it to your Mac:  rsync quesihealy@fomoroller.local:{OUT_PATH} .")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted")
