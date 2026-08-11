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

MPU_ADDR = 0x69          # 0x69, not 0x68 — 0x68 is the PiSugar 3's RTC
OUT_PATH = "/home/pi/roll_calib.csv"
SAMPLE_HZ = 20

# MPU-6050 sensitivity at default full-scale ranges
ACCEL_LSB_PER_G   = 16384.0     # ±2 g
GYRO_LSB_PER_DPS  = 131.0       # ±250 °/s

# Timed routine: (seconds, label, instruction). Do exactly what each says.
PHASES = [
    (8,  "still",         "Set it down and DON'T touch it"),
    (12, "roll",          "ROLL it back and forth like you're using it"),
    (10, "pickup_walk",   "Pick it up and carry it around / reposition it"),
    (8,  "bump",          "Nudge, tap, and bump it WITHOUT rolling"),
    (6,  "setdown_still", "Set it down and leave it still again"),
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
    bus.write_byte_data(MPU_ADDR, 0x6B, 0)   # wake the sensor
    time.sleep(0.1)

    total = sum(p[0] for p in PHASES)
    print(f"Logging {total}s of data to {OUT_PATH}")
    print("Follow each on-screen instruction. Starting in 3s...\n")
    time.sleep(3)

    dt = 1.0 / SAMPLE_HZ
    with open(OUT_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "ax", "ay", "az", "gx", "gy", "gz", "label"])

        t0 = time.time()
        for duration, label, instruction in PHASES:
            print(f"\n>>> {label.upper()}: {instruction}  ({duration}s)")
            phase_end = time.time() + duration
            while time.time() < phase_end:
                t = time.time() - t0
                ax, ay, az, gx, gy, gz = read_all(bus)
                w.writerow([f"{t:.2f}", ax, ay, az, gx, gy, gz, label])
                # live gyro readout in deg/s so you can eyeball the roll axis
                print(f"  t={t:5.1f}  gyro(°/s) "
                      f"x={gx / GYRO_LSB_PER_DPS:7.1f} "
                      f"y={gy / GYRO_LSB_PER_DPS:7.1f} "
                      f"z={gz / GYRO_LSB_PER_DPS:7.1f}", end="\r")
                time.sleep(dt)

    print(f"\n\nDone. Saved {OUT_PATH}")
    print("Copy it to your Mac:  rsync pi@fomoroller.local:/home/pi/roll_calib.csv .")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted")
