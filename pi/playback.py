#!/usr/bin/env python3
"""
FOMO Roller — Pi Playback Script
==================================
Runs on the Raspberry Pi 4 inside the foam roller.
Set this up to auto-start on boot via systemd.

Behaviour:
  - Polls the MPU-6050 gyroscope 20x/second and detects *rolling*
    specifically: sustained rotation about the roller's long axis (high
    |gy|) while the other axes stay low. This ignores being picked up,
    carried, or bumped — those tumble all axes and don't trigger playback.
  - When rolling detected: fade in and play the current 30-min slot MP3
  - When roller stops for PAUSE_TIMEOUT seconds: fade out and pause
  - When rolling resumes after a SHORT pause (< REINTRO_IDLE_SEC): rewind
    REWIND_MS and fade back in where it left off
  - When rolling resumes after a LONG idle (>= REINTRO_IDLE_SEC): play the
    slot's opener MP3 ("You are missing out on N events…") first, then the
    event readout from the top
  - When a new 30-min slot starts mid-session: seamlessly switch files

Dependencies:
    sudo apt install vlc
    pip3 install -r requirements.txt   # python-vlc, smbus2
"""

import vlc
import time
import smbus2
import threading
import logging
import os
import collections
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────

# Audio location. Defaults to ~/audio for whoever runs the script; the
# systemd unit sets FOMO_AUDIO_DIR explicitly to override it.
AUDIO_DIR         = os.environ.get("FOMO_AUDIO_DIR", os.path.expanduser("~/audio"))
BM_TZ             = ZoneInfo("America/Los_Angeles")

# TESTING: set FOMO_TEST_FILE to a filename in AUDIO_DIR to always play that
# one file, ignoring the clock. Unset for normal 30-min time-slot playback.
# (Its matching <slot>_opener.mp3 is used for the opener automatically.)
TEST_AUDIO_FILE   = os.environ.get("FOMO_TEST_FILE")

# Played when the current slot has no MP3 — outside the event, or a gap with no
# events. A short "nothing happening right now" message so a roll isn't just
# silence. Set FOMO_FALLBACK_FILE="" to stay silent instead. Has no opener.
FALLBACK_FILE     = os.environ.get("FOMO_FALLBACK_FILE", "no_events.mp3")

# Roll detection (gyroscope-based). Rolling is sustained rotation about the
# roller's long axis: a high roll-axis rate (|gy|) while the other two axes
# stay low. Carrying/bumping tumbles all axes and is rejected. Thresholds
# came from calibrate_motion.py — re-run it if you remount the sensor.
MPU_ADDR          = 0x69        # I2C address — 0x68 collides with the PiSugar
                                 # 3's RTC (see SETUP.md step 3), so AD0 is
                                 # pulled high on this build to land on 0x69
GY_ON_DPS         = 70          # roll-axis rate (°/s) that counts as rolling
OFF_MAX_DPS       = 45          # if off-axis rate (°/s) exceeds this it's
                                # being carried/tumbled, not rolled — ignore
GYRO_LSB_PER_DPS  = 131.0       # MPU-6050 scale at ±250 °/s full-scale
SMOOTH_WINDOW     = 15          # sliding-median length (~0.75s at 20Hz);
                                # rejects single-sample glitches and bumps
POLL_INTERVAL     = 0.05        # seconds between checks (20Hz)

# Playback
PAUSE_TIMEOUT     = 2.0         # seconds stopped before it pauses
ROLL_START_SEC    = 1.0         # must roll this long before (re)starting
                                # playback — ignores quick brushes/bumps
REWIND_MS         = 4000        # rewind this many ms on resume
REINTRO_IDLE_SEC  = 10          # if the roller's been still at least this long,
                                # replay the slot's opener before the events
                                # instead of resuming where it left off
SLOT_MINUTES      = 30

# Volume
MAX_VOLUME        = 100         # 0–200; 100 = unity, push higher if needed
FADE_IN_DURATION  = 1.5         # seconds for a full fade in (smooth start)
FADE_OUT_DURATION = 1.0         # seconds for a full fade out
FADE_STEPS        = 20

# Bluetooth — speaker MAC comes from the environment (see the systemd unit)
BT_DEVICE_MAC     = os.environ.get("FOMO_BT_MAC", "XX:XX:XX:XX:XX:XX")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("fomo")

# ── MPU-6050 ──────────────────────────────────────────────────────────────────

def init_mpu(bus):
    """Wake the MPU-6050 and configure the gyro for roll detection."""
    bus.write_byte_data(MPU_ADDR, 0x6B, 0x00)   # wake (starts in sleep mode)
    time.sleep(0.1)
    bus.write_byte_data(MPU_ADDR, 0x1A, 0x03)   # DLPF ~44Hz — cut HF noise
    bus.write_byte_data(MPU_ADDR, 0x1B, 0x00)   # gyro full-scale ±250 °/s
    time.sleep(0.1)
    log.info("MPU-6050 ready")

def read_gyro(bus):
    """Return (gx, gy, gz) raw counts from one block read (single sample)."""
    d = bus.read_i2c_block_data(MPU_ADDR, 0x43, 6)
    def s16(hi, lo):
        v = (hi << 8) | lo
        return v - 65536 if v >= 32768 else v
    return s16(d[0], d[1]), s16(d[2], d[3]), s16(d[4], d[5])


class RollDetector:
    """
    Detects rolling from the gyro: a sustained high roll-axis rate (|gy|)
    while the off-axis rate (|gx|+|gz|) stays low. Uses a sliding median so a
    single torn read or a one-off bump can't trigger it — the rate has to hold
    across the window. Roll-axis saturation (±250 °/s) just reads as "high".
    """

    def __init__(self):
        self._gy  = collections.deque(maxlen=SMOOTH_WINDOW)
        self._off = collections.deque(maxlen=SMOOTH_WINDOW)

    def update(self, gx, gy, gz):
        self._gy.append(min(abs(gy), 32767) / GYRO_LSB_PER_DPS)
        self._off.append((abs(gx) + abs(gz)) / GYRO_LSB_PER_DPS)
        if len(self._gy) < SMOOTH_WINDOW:
            return False
        return median(self._gy) > GY_ON_DPS and median(self._off) < OFF_MAX_DPS

# ── Audio file helpers ────────────────────────────────────────────────────────

def current_slot_file():
    """Path of the event-readout MP3 for the current 30-min slot.

    If that slot has no MP3 (outside the event, or a gap with no events), fall
    back to FALLBACK_FILE when it exists, otherwise return the missing slot
    path so the player just stays silent."""
    if TEST_AUDIO_FILE:
        return os.path.join(AUDIO_DIR, TEST_AUDIO_FILE)
    now    = datetime.now(BM_TZ)
    minute = 0 if now.minute < SLOT_MINUTES else SLOT_MINUTES
    slot   = now.replace(minute=minute, second=0, microsecond=0)
    path   = os.path.join(AUDIO_DIR, slot.strftime("%Y-%m-%d_%H-%M.mp3"))
    if not os.path.exists(path) and FALLBACK_FILE:
        fallback = os.path.join(AUDIO_DIR, FALLBACK_FILE)
        if os.path.exists(fallback):
            return fallback
    return path

def current_opener_file():
    """The opener MP3 for the current slot: '<slot>.mp3' -> '<slot>_opener.mp3'."""
    return current_slot_file()[: -len(".mp3")] + "_opener.mp3"

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
        self._current_file = None    # file literally loaded (opener OR body)
        self._slot_file    = None    # the slot's body file — its identity,
                                     # even while its opener is what's playing

        log.info("VLC player ready")

    # ── Internal fade ──────────────────────────────────────────────────────

    def _fade(self, direction, cancel, callback=None):
        """
        Ramp volume up ('in') or down ('out'). Fade-in and fade-out have
        separate durations. Cancellable: checks the cancel event it was
        started with between steps (never a newer one, so a straggler thread
        stays cancelled).
        """
        duration   = FADE_IN_DURATION if direction == "in" else FADE_OUT_DURATION
        step_sleep = duration / FADE_STEPS
        if direction == "in":
            volumes = [int(MAX_VOLUME * i / FADE_STEPS) for i in range(1, FADE_STEPS + 1)]
        else:
            volumes = [int(MAX_VOLUME * i / FADE_STEPS) for i in range(FADE_STEPS - 1, -1, -1)]

        for v in volumes:
            if cancel.is_set():
                return
            self._player.audio_set_volume(v)
            time.sleep(step_sleep)

        if callback and not cancel.is_set():
            callback()

    def _cancel_fade(self):
        """Stop any in-progress fade (and its pending callback) synchronously."""
        self._fade_cancel.set()
        if self._fade_thread and self._fade_thread.is_alive():
            self._fade_thread.join(timeout=FADE_IN_DURATION + 0.2)

    def _start_fade(self, direction, callback=None):
        """Cancel any in-progress fade and start a new one."""
        self._cancel_fade()
        self._fade_cancel = threading.Event()
        t = threading.Thread(
            target=self._fade,
            args=(direction, self._fade_cancel, callback),
            daemon=True
        )
        self._fade_thread = t
        t.start()

    # ── Public interface ───────────────────────────────────────────────────

    def play(self, filepath, seek_ms=0, fade=True):
        """Load the slot's body file and start playing.

        fade=True fades in (for genuine (re)starts — picking the roller up). Set
        fade=False to start at full volume, used for the opener->body handoff so
        the events continue seamlessly instead of re-fading after the opener."""
        # Kill any fade-out first so its pause callback can't land after play()
        self._cancel_fade()

        # This is the active slot even if the file is missing — set it before
        # the existence check so the main loop stops re-triggering play().
        self._slot_file = filepath

        if not os.path.exists(filepath):
            # Remember it anyway so the main loop doesn't retry (and warn)
            # 10x/second for the whole slot — e.g. outside event week
            if filepath != self._current_file:
                log.warning(f"Audio file not found: {filepath}")
                self._current_file = filepath
            return

        media = self._instance.media_new(filepath)
        self._player.set_media(media)
        self._player.audio_set_volume(0 if fade else MAX_VOLUME)
        self._player.play()
        self._current_file = filepath

        if seek_ms > 0:
            time.sleep(0.3)     # give VLC a moment to open the stream
            self._player.set_time(seek_ms)
            log.info(f"Resuming {os.path.basename(filepath)} at {seek_ms/1000:.1f}s")
        else:
            log.info(f"Playing {os.path.basename(filepath)}")

        if fade:
            self._start_fade("in")

    def play_opener(self, opener_path, body_path):
        """Play the slot's opener with a fade in, then let the main loop advance
        to the body once it finishes. The active slot is the *body* file, so a
        slot change is still detected and the main loop's "finished" branch
        rolls straight from a finished opener into the body."""
        self._cancel_fade()

        # No opener for this slot (e.g. not generated) — just play the body.
        if not os.path.exists(opener_path):
            self.play(body_path)
            return

        media = self._instance.media_new(opener_path)
        self._player.set_media(media)
        # Start at full volume — no fade-in, so the short opener isn't muffled
        self._player.audio_set_volume(MAX_VOLUME)
        self._player.play()
        self._current_file = opener_path
        self._slot_file    = body_path
        log.info(f"Playing opener {os.path.basename(opener_path)}")

    def pause(self):
        """Fade out then pause, preserving playback position."""
        log.info("Pausing (fade out)")
        self._start_fade("out", callback=self._player.pause)

    def resume(self):
        """
        Resume from current position minus REWIND_MS, with fade in.
        Called when the roller starts moving again after a pause.
        """
        # Kill an in-flight fade-out first, otherwise its pause callback can
        # fire just after our play() and leave the player silently paused
        self._cancel_fade()

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

    def active_slot_file(self):
        """The body file of the slot currently loaded (opener or body)."""
        return self._slot_file

    def playing_opener(self):
        """True while the loaded file is the slot's opener (not its body yet)."""
        return self._current_file is not None and self._current_file != self._slot_file

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    log.info("FOMO Roller starting up")

    bus = smbus2.SMBus(1)
    init_mpu(bus)

    player         = FomoPlayer()
    detector       = RollDetector()
    last_motion    = 0.0
    roll_start     = None        # when the current roll began (None if stopped)
    is_paused      = False
    idle_before    = 0.0         # seconds the roller sat still before this roll

    log.info("Listening for rolling...")

    while True:
        now     = time.time()
        gx, gy, gz = read_gyro(bus)
        rolling = detector.update(gx, gy, gz)

        if rolling:
            if roll_start is None:
                roll_start  = now
                # How long it sat still before this roll began. Captured once,
                # here, because last_motion is about to advance every frame.
                idle_before = now - last_motion
            last_motion = now

            # Only (re)start once rolling has been sustained long enough — a
            # quick brush or bump won't clear this bar. Brief drop-outs mid-
            # roll (turnarounds) don't reset roll_start; only a real stop does
            # (handled below via PAUSE_TIMEOUT), so this doesn't re-arm here.
            if (now - roll_start) >= ROLL_START_SEC:
                body_file   = current_slot_file()
                opener_file = current_opener_file()
                long_idle   = idle_before >= REINTRO_IDLE_SEC

                if body_file != player.active_slot_file():
                    # New/first time slot
                    if long_idle:
                        player.play_opener(opener_file, body_file)
                    else:
                        player.play(body_file)
                    is_paused = False

                elif long_idle:
                    # Same slot, but the roller sat idle a good while: replay
                    # the opener, then roll into the events from the top
                    player.play_opener(opener_file, body_file)
                    is_paused = False

                elif is_paused:
                    # Same slot, short pause — resume where we left off
                    player.resume()
                    is_paused = False

                elif player.state() not in (vlc.State.Playing, vlc.State.Opening):
                    if player.playing_opener():
                        # Opener just finished -> roll straight into the events
                        # at full volume (no re-fade after the opener)
                        player.play(body_file, fade=False)
                    else:
                        # Events finished while still rolling -> loop from the
                        # top, replaying the opener so the count is re-announced
                        player.play_opener(opener_file, body_file)
                    is_paused = False

                # The opener/resume decision is a one-shot: spend it so the
                # frames that follow (still rolling) don't keep re-triggering.
                idle_before = 0.0

        else:
            # Roller is still — after PAUSE_TIMEOUT, pause and disarm the
            # roll timer so the next start needs a fresh full second of rolling
            if (now - last_motion) > PAUSE_TIMEOUT:
                roll_start = None
                if not is_paused and player.state() == vlc.State.Playing:
                    player.pause()
                    is_paused = True

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
