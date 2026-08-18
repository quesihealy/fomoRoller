#!/usr/bin/env python3
"""
FOMO Roller — I2C Bus Health Check
==================================
Quick diagnostic to confirm the MPU-6050 reads are clean over I2C — i.e. that
the pogo-pin/ground fix worked and the bus isn't tearing anymore. Sit the
roller STILL on a table and run it; it doesn't need the full roll routine.

Two independent tests:
  1. WHO_AM_I integrity — reads the sensor's fixed ID register many times.
     The value is a known constant (0x68), so ANY other value is a corrupted
     transaction. This is the cleanest possible bus-integrity signal.
  2. Data sanity + tear rate — reads accel+gyro while still: accel magnitude
     should sit near 1g (16384) and gyro near zero; any full-scale (±32767)
     value is a torn read.

Use it to compare baudrates: run at the current speed, then bump the baudrate
back up in config.txt, reboot, and run again. If both pass, the hardware fix
holds and you can keep the faster bus.

    python3 i2c_healthcheck.py
"""

import smbus2
import time
import math

MPU_ADDR = 0x69          # 0x69, not 0x68 — 0x68 is the PiSugar 3's RTC
WHO_AM_I = 0x75
EXPECTED_ID = 0x68       # MPU-6050 fixed device ID (independent of AD0/address)

ID_READS = 500           # how many WHO_AM_I reads for the integrity test
DATA_SECONDS = 10        # how long to sample accel+gyro
DATA_HZ = 50

ACCEL_LSB_PER_G  = 16384.0
GYRO_LSB_PER_DPS = 131.0
TEAR_LIMIT = 32000       # |raw| >= this ⇒ corrupted transaction


def read_word(bus, reg):
    d = bus.read_i2c_block_data(MPU_ADDR, reg, 2)
    v = (d[0] << 8) | d[1]
    return v - 65536 if v >= 32768 else v


def main():
    bus = smbus2.SMBus(1)
    bus.write_byte_data(MPU_ADDR, 0x6B, 0)      # wake
    time.sleep(0.1)
    bus.write_byte_data(MPU_ADDR, 0x1A, 0x03)   # DLPF ~44Hz
    bus.write_byte_data(MPU_ADDR, 0x1B, 0x00)   # gyro ±250
    bus.write_byte_data(MPU_ADDR, 0x1C, 0x00)   # accel ±2g
    time.sleep(0.1)

    print("Keep the roller STILL for ~10s...\n")

    # ── Test 1: WHO_AM_I integrity ─────────────────────────────────────────
    id_bad = 0
    for _ in range(ID_READS):
        try:
            val = bus.read_byte_data(MPU_ADDR, WHO_AM_I)
        except OSError:
            val = -1                             # bus error = a bad read
        if val != EXPECTED_ID:
            id_bad += 1
    id_pct = 100 * id_bad / ID_READS
    print(f"1) WHO_AM_I integrity : {ID_READS - id_bad}/{ID_READS} good "
          f"({id_pct:.1f}% corrupt)")

    # ── Test 2: data tear rate + still-noise sanity ────────────────────────
    n = tears = 0
    mags, gnoise = [], []
    dt = 1.0 / DATA_HZ
    end = time.time() + DATA_SECONDS
    while time.time() < end:
        try:
            ax, ay, az = (read_word(bus, 0x3B), read_word(bus, 0x3D),
                          read_word(bus, 0x3F))
            gx, gy, gz = (read_word(bus, 0x43), read_word(bus, 0x45),
                          read_word(bus, 0x47))
        except OSError:
            tears += 1
            n += 1
            time.sleep(dt)
            continue
        n += 1
        if any(abs(v) >= TEAR_LIMIT for v in (ax, ay, az, gx, gy, gz)):
            tears += 1
            continue
        mags.append(math.sqrt(ax * ax + ay * ay + az * az) / ACCEL_LSB_PER_G)
        gnoise.append(max(abs(gx), abs(gy), abs(gz)) / GYRO_LSB_PER_DPS)
    tear_pct = 100 * tears / max(1, n)
    print(f"2) Data tear rate     : {tears}/{n} torn ({tear_pct:.1f}%)")
    if mags:
        avg_g = sum(mags) / len(mags)
        max_gyro = max(gnoise)
        print(f"   accel magnitude   : {avg_g:.3f} g   (want ~1.00 when still)")
        print(f"   gyro max while still: {max_gyro:.1f} °/s (want < ~3)")

    # ── Verdict ────────────────────────────────────────────────────────────
    clean = id_pct < 1.0 and tear_pct < 1.0
    print("\n" + ("PASS — bus looks clean." if clean else
                  "FAIL — still seeing corruption; connection not fully fixed."))
    if mags and not (0.9 <= (sum(mags) / len(mags)) <= 1.1):
        print("Note: accel magnitude is off ~1g — check the sensor was held still.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted")
