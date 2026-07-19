#!/usr/bin/env python3
"""
FOMO Roller — Pi Playback Script
==================================
Runs on the Raspberry Pi Zero 2W inside the foam roller.
Set this up to auto-start on boot via systemd.

Behaviour:
  - Polls MPU-6050 for motion 10x/second
  - When motion detected: fade in and play the current 30-min slot MP3
  - When roller stops for PAUSE_TIMEOUT seconds: fade out and pause
  - When rolling resumes: rewind REWIND_MS and fade back in
  - When a new 30-min slot starts mid-session: seamlessly switch files

Dependencies:
    sudo apt install vlc python3-vlc python3-smbus
    pip3 install smbus2
"""

import vlc
import time
import smbus2
import threading
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────

AUDIO_DIR         = "/home/pi/audio"
BM_TZ             = ZoneInfo("America/Los_Angeles")

# Motion detection
MPU_ADDR          = 0x68        # I2C address — pull AD0 high for 0x69
MOTION_THRESHOLD  = 800         # tune this: higher = less sensitive
POLL_INTERVAL     = 0.1         # seconds between motion checks (10Hz)

# Playback
PAUSE_TIMEOUT     = 2.0         # seconds still before pausing
REWIND_MS         = 4000        # rewind this many ms on resume
SLOT_MINUTES      = 30

# Volume
MAX_VOLUME        = 100         # 0–200; 100 = unity, push higher if needed
FADE_DURATION     = 1.5         # seconds for a full fade in or out
FADE_STEPS        = 20

# Bluetooth — replace with your speaker's MAC address
BT_DEVICE_MAC     = "XX:XX:XX:XX:XX:XX"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("fomo")

# ── MPU-6050 ──────────────────────────────────────────────────────────────────

def init_mpu(bus):
    """Wake the MPU-6050 (it starts in sleep mode)."""
    bus.write_byte_data(MPU_ADDR, 0x6B, 0)
    time.sleep(0.1)
    log.info("MPU-6050 ready")

def read_accel_z(bus):
    high = bus.read_byte_data(MPU_ADDR, 0x3F)
    low  = bus.read_byte_data(MPU_ADDR, 0x40)
    val  = (high << 8) | low
    return val - 65536 if val > 32767 else val

def calibrate_baseline(bus, samples=50):
    """Average a few readings at rest to establish baseline."""
    log.info("Calibrating motion baseline — keep roller still...")
    readings = [read_accel_z(bus) for _ in range(samples)]
    baseline = sum(readings) // len(readings)
    log.info(f"  Baseline: {baseline}")
    return baseline

# ── Audio file helpers ────────────────────────────────────────────────────────

def current_slot_file():
    """Return the path of the MP3 for the current 30-min slot."""
    now    = datetime.now(BM_TZ)
    minute = 0 if now.minute < SLOT_MINUTES else SLOT_MINUTES
    slot   = now.replace(minute=minute, second=0, microsecond=0)
    fname  = slot.strftime("%Y-%m-%d_%H-%M.mp3")
    return os.path.join(AUDIO_DIR, fname)

# ── VLC player wrapper ────────────────────────────────────────────────────────

class FomoPlayer:
    """
    Wraps a VLC media player with fade in/out and resume-with-rewind support.
    All fades run on a background daemon thread so the motion loop never blocks.
    """

    def __init__(self):
        # Route audio to Bluetooth via ALSA/bluealsa
        alsa_device = f"bluealsa:DEV={BT_DEVICE_MAC},PROFILE=a2dp"
        self._instance = vlc.Instance(f"--aout=alsa --alsa-audio-device={alsa_device}")
        self._player   = self._instance.media_player_new()
        self._player.audio_set_volume(0)

        self._fade_thread  = None
        self._fade_cancel  = threading.Event()
        self._current_file = None

        log.info("VLC player ready")

    # ── Internal fade ──────────────────────────────────────────────────────

    def _fade(self, direction, callback=None):
        """
        Ramp volume up ('in') or down ('out') over FADE_DURATION seconds.
        Cancellable: checks self._fade_cancel between steps.
        """
        step_sleep = FADE_DURATION / FADE_STEPS
        if direction == "in":
            volumes = [int(MAX_VOLUME * i / FADE_STEPS) for i in range(1, FADE_STEPS + 1)]
        else:
            volumes = [int(MAX_VOLUME * i / FADE_STEPS) for i in range(FADE_STEPS - 1, -1, -1)]

        for v in volumes:
            if self._fade_cancel.is_set():
                return
            self._player.audio_set_volume(v)
            time.sleep(step_sleep)

        if callback and not self._fade_cancel.is_set():
            callback()

    def _start_fade(self, direction, callback=None):
        """Cancel any in-progress fade and start a new one."""
        self._fade_cancel.set()
        if self._fade_thread and self._fade_thread.is_alive():
            self._fade_thread.join(timeout=FADE_DURATION + 0.2)

        self._fade_cancel = threading.Event()
        t = threading.Thread(
            target=self._fade,
            args=(direction, callback),
            daemon=True
        )
        self._fade_thread = t
        t.start()

    # ── Public interface ───────────────────────────────────────────────────

    def play(self, filepath, seek_ms=0):
        """Load a file and start playing with a fade in, optionally seeking first."""
        if not os.path.exists(filepath):
            log.warning(f"Audio file not found: {filepath}")
            return

        media = self._instance.media_new(filepath)
        self._player.set_media(media)
        self._player.audio_set_volume(0)
        self._player.play()
        self._current_file = filepath

        if seek_ms > 0:
            time.sleep(0.3)     # give VLC a moment to open the stream
            self._player.set_time(seek_ms)
            log.info(f"Resuming {os.path.basename(filepath)} at {seek_ms/1000:.1f}s")
        else:
            log.info(f"Playing {os.path.basename(filepath)}")

        self._start_fade("in")

    def pause(self):
        """Fade out then pause, preserving playback position."""
        log.info("Pausing (fade out)")
        self._start_fade("out", callback=self._player.pause)

    def resume(self):
        """
        Resume from current position minus REWIND_MS, with fade in.
        Called when the roller starts moving again after a pause.
        """
        pos        = self._player.get_time()
        resume_pos = max(0, pos - REWIND_MS)
        log.info(f"Resuming with rewind: {pos/1000:.1f}s → {resume_pos/1000:.1f}s")
        self._player.set_time(resume_pos)
        self._player.audio_set_volume(0)
        self._player.play()
        self._start_fade("in")

    def state(self):
        return self._player.get_state()

    def current_file(self):
        return self._current_file

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("FOMO Roller starting up")

    bus      = smbus2.SMBus(1)
    init_mpu(bus)
    baseline = calibrate_baseline(bus)

    player         = FomoPlayer()
    last_motion    = 0.0
    is_paused      = False

    log.info("Listening for motion...")

    while True:
        now   = time.time()
        z     = read_accel_z(bus)
        moving = abs(z - baseline) > MOTION_THRESHOLD

        if moving:
            last_motion = now
            audio_file  = current_slot_file()

            if audio_file != player.current_file():
                # New time slot — start fresh
                player.play(audio_file)
                is_paused = False

            elif is_paused:
                # Same slot, resuming after stillness
                player.resume()
                is_paused = False

            elif player.state() not in (vlc.State.Playing, vlc.State.Opening):
                # Finished playing — restart from top
                player.play(audio_file)
                is_paused = False

        else:
            # Roller is still
            if not is_paused and (now - last_motion) > PAUSE_TIMEOUT:
                if player.state() == vlc.State.Playing:
                    player.pause()
                    is_paused = True

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
