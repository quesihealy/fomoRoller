# Pi setup

One-time setup for the Raspberry Pi that lives inside the roller. Assumes
the hardware in the project's parts list: Pi 4B, PiSugar 3 Plus, a 32GB
microSD card, and an MPU-6050 breakout — plus a Bluetooth speaker (not on
the parts list yet; `playback.py` only knows how to output over Bluetooth).

## 1. Flash the OS

Use Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)** (no desktop
needed; keeps the 1GB Pi 4's RAM free for VLC). In the imager's advanced
options (gear icon), set the hostname, enable SSH, and set your Wi-Fi
(needed for this setup only — the roller runs offline on the playa).

## 2. Enable I2C

```sh
sudo raspi-config
# Interface Options → I2C → enable
```

## 3. Wire up the MPU-6050 and set the PiSugar's I2C address

PiSugar 3 Plus connects via pogo pins from the back of the Pi and doesn't
occupy the GPIO header, so the MPU-6050 can be wired to the same I2C bus
(SDA/SCL/3V3/GND) without a conflict — mechanically.

**Address conflict to avoid:** the PiSugar 3 occupies **both** `0x57` and
`0x68` by default — `0x57` for power management, `0x68` for its RTC — it's
not an either/or choice despite how the docs read. The MPU-6050 also
defaults to `0x68` (`0x69` if `AD0` is pulled high), so out of the box it
collides with the PiSugar's RTC. Rather than reconfiguring the PiSugar,
pull the MPU-6050's `AD0` pin high so it lands on `0x69` instead — that's
what `playback.py`'s `MPU_ADDR` assumes.

Check all three are visible and at distinct addresses:

```sh
sudo apt install i2c-tools
i2cdetect -y 1
# expect 0x57 (PiSugar power), 0x68 (PiSugar RTC), 0x69 (MPU-6050)
```

If you read `0x68` here expecting it to be the MPU-6050, you'll get
frozen/zero deltas out of it — you're actually talking to the PiSugar's
RTC, which doesn't have a Z-accel register at that offset.

## 4. Make the PiSugar RTC the system clock source

There's no internet on the playa, so the Pi can't get the time from NTP —
and `playback.py` picks which MP3 to play from `datetime.now()`. Without a
working RTC, every power cycle resets the clock and the roller plays the
wrong slot until someone fixes it by hand.

**Important:** the PiSugar 3's RTC is *not* a standard I2C RTC chip — it's
managed by the PiSugar's onboard microcontroller (at I2C `0x68`), and the
generic `dtoverlay=i2c-rtc` kernel driver does **not** work with it. There is
no `/dev/rtc` and `hwclock` won't see it. The supported path is PiSugar's own
`pisugar-server` software, plus a small boot-time service (in this repo) that
copies the RTC time into the system clock on every boot.

Install `pisugar-server` (official installer; sets up the service + a web UI
on `:8421`):

```sh
wget -O pisugar-power-manager.sh https://cdn.pisugar.com/release/pisugar-power-manager.sh \
  && bash pisugar-power-manager.sh -c release
systemctl status pisugar-server   # expect active (running)
```

Seed the RTC with the correct time while online (NTP has set the clock), using
the helper in this repo (talks to pisugar-server's command socket on `:8423`):

```sh
python3 /home/quesihealy/fomo-roller/rtc_sync.py rtc_pi2rtc   # system -> RTC
```

Install the boot-sync unit so every power-up sets the system clock *from* the
RTC (`rtc_rtc2pi`), ordered before `fomo-roller`:

```sh
sudo cp service/pisugar-rtc-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pisugar-rtc-sync.service
```

Verify the offline behaviour — disable NTP, set a wrong time, reboot, and
confirm the RTC fixes it with no network:

```sh
sudo timedatectl set-ntp false
sudo date -s "2020-01-01 12:00:00"
sudo reboot
# after reboot:
date                                          # should be correct again
journalctl -b -u pisugar-rtc-sync.service     # shows the boot-time sync
sudo timedatectl set-ntp true                 # re-enable NTP for setup use
```

Re-seed the RTC (`rtc_pi2rtc`) once more right before deploying, so it starts
the event with a fresh, NTP-accurate time.

Reference: [PiSugar Power Manager](https://github.com/PiSugar/pisugar-power-manager-rs).

**Clean shutdown button.** Map a PiSugar button **double-tap** to a clean
`shutdown`, so the roller can be powered off safely in the field without SSH
(a long *hold* is a hardware power cut — avoid it, it risks SD corruption).
pisugar-server runs the command as root:

```sh
sudo python3 - <<'PY'
import json
p = "/etc/pisugar-server/config.json"
d = json.load(open(p))
d["double_tap_enable"] = True
d["double_tap_shell"]  = "shutdown -h now"
json.dump(d, open(p, "w"), indent=2)
PY
sudo systemctl restart pisugar-server
# Test: double-tap the button -> the Pi halts cleanly. (Single tap just shows
# the battery level on the LEDs.)
```

## 5. Pair the Bluetooth speaker

```sh
bluetoothctl
> scan on            # note the speaker's MAC address, then:
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> connect XX:XX:XX:XX:XX:XX
> exit
```

Install the audio bridge `playback.py` expects:

```sh
sudo apt install vlc bluez-alsa-utils
```

(Raspberry Pi OS Bookworm ships `bluez-alsa-utils`, the successor to the
standalone `bluealsa` package the code's docstring originally referenced.)

## 6. Install the Python deps and code

```sh
mkdir -p /home/quesihealy/fomo-roller
# copy playback.py, requirements.txt, and service/ here
python3 -m venv /home/quesihealy/fomo-roller/venv
/home/quesihealy/fomo-roller/venv/bin/pip install -r /home/quesihealy/fomo-roller/requirements.txt
```

Update `ExecStart` in `service/fomo-roller.service` to use the venv's
python if you don't install system-wide.

## 7. Copy the audio files

From the Mac, after running the generator:

```sh
rsync -avz --progress audio/ quesihealy@<pi-hostname>.local:/home/quesihealy/audio/
```

## 8. Install the systemd services

Four units: `fomo-bt-connect` reliably connects the Bluetooth speaker at boot
(retrying, and clearing the rfkill soft-block), `fomo-bt-watchdog` keeps it
connected for the rest of the session, `fomo-battery-guard` cleanly shuts down
on low battery, and `fomo-roller` runs the playback — ordered to start only
once the speaker and `bluealsa` are up. Set `FOMO_BT_MAC` to your speaker's
address in the **three BT units** (the battery guard needs no MAC).

```sh
sudo cp service/fomo-bt-connect.service service/fomo-bt-watchdog.service \
  service/fomo-battery-guard.service service/fomo-roller.service /etc/systemd/system/
sudo sed -i 's/XX:XX:XX:XX:XX:XX/AA:BB:CC:DD:EE:FF/' \
  /etc/systemd/system/fomo-bt-connect.service \
  /etc/systemd/system/fomo-bt-watchdog.service \
  /etc/systemd/system/fomo-roller.service
sudo systemctl enable bluealsa                     # audio bridge, on boot
sudo systemctl daemon-reload
sudo systemctl enable --now fomo-bt-connect fomo-bt-watchdog \
  fomo-battery-guard fomo-roller
journalctl -u fomo-roller -u fomo-bt-watchdog -f   # watch logs
```

The `pisugar-rtc-sync` unit from step 4 also runs before `fomo-roller`, so the
clock is correct before playback picks an MP3 slot.

**Self-healing audio:** if the speaker dies mid-event and you power it back
on, `fomo-bt-watchdog` reconnects it within a few seconds and then restarts
`fomo-roller`, so VLC re-opens the freshly re-exposed bluealsa sink (a running
player can't recover a PCM that disappeared). Test it: with everything running,
power the speaker off, wait, power it back on, and confirm audio returns on the
next roll — watch `journalctl -u fomo-bt-watchdog -f` for the drop/reconnect.

**Clean shutdown on low battery:** `fomo-battery-guard` polls the PiSugar via
pisugar-server and, when the charge drops below ~15% (or ~3.4 V) while not
charging, issues a clean `shutdown` so the SD card unmounts safely instead of
the battery hard-cutting power. Thresholds are tunable via `FOMO_BATT_MIN` /
`FOMO_BATT_VMIN` in the unit. Watch `journalctl -u fomo-battery-guard -f`.

## 9. Tune roll detection

`playback.py` detects *rolling* from the gyroscope: `GY_ON_DPS` (roll-axis
rate to count as rolling) and `OFF_MAX_DPS` (off-axis rate above which it's
being carried, not rolled). Defaults came from `calibrate_motion.py`. If you
remount the sensor, re-run that tool and adjust; watch
`journalctl -u fomo-roller -f` for false triggers or missed rolls.
`ROLL_START_SEC` sets how long you must roll before it (re)starts.

## 10. Field power diet (WiFi + LEDs off)

The playa build is fully offline, so the WiFi radio is dead weight — turning it
off saves battery. Bluetooth stays on for audio, and the Pi's status LEDs are
left on as an at-a-glance "is it alive" indicator (they cost negligible power,
and with SSH gone they're your only remote signal). `field_mode.sh` toggles a
marked block in the boot config:

```sh
/home/quesihealy/fomo-roller/field_mode.sh on    # WiFi + LEDs off, then reboot
```

**This disables WiFi — you'll lose SSH after the reboot.** Do it near the end,
once you no longer need remote access, and **before** the overlay freeze (step
11), since the boot config must still be writable. To undo: run `field_mode.sh
off` from a local console (keyboard + monitor), or pop the SD card into any
computer and delete the `fomo-field-diet` block from the boot `config.txt`
(it's the FAT boot partition, readable anywhere).

## 11. Freeze the build (overlay filesystem)

**Do this last** — only once everything above works and is tested: audio loaded
(step 7), all four services deployed and cold-boot tested (step 8), the speaker
paired (step 5), and the RTC freshly seeded. The overlay makes the root
filesystem read-only and sends all runtime writes to a RAM overlay that's
discarded on reboot, so power loss and flash wear can't corrupt the card — the
roller's single biggest failure mode on the playa.

First cap the logs, so a week of uptime doesn't fill the RAM overlay on the 1GB
Pi (with the overlay on, journald writes land in RAM):

```sh
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=50M\n' \
  | sudo tee /etc/systemd/journald.conf.d/volatile.conf
sudo systemctl restart systemd-journald
```

Re-seed the RTC one last time while online so playback starts on an accurate
clock (see step 4), then enable the overlay:

```sh
python3 /home/quesihealy/fomo-roller/rtc_sync.py rtc_pi2rtc   # system -> RTC
sudo raspi-config     # Performance Options -> Overlay File System -> enable,
                      # and write-protect the boot partition when prompted
sudo reboot
# non-interactive equivalent: sudo raspi-config nonint enable_overlayfs
```

Confirm it's active after reboot:

```sh
findmnt / | grep -q overlay && echo "root is read-only (overlay active)"
```

**To change anything later** (new audio, code, config, `apt`) you must lift the
freeze first — the card is read-only:

```sh
sudo raspi-config     # Performance Options -> Overlay File System -> disable
sudo reboot
# ... rsync new audio / edit code / etc ...
sudo raspi-config     # re-enable Overlay File System
sudo reboot
```

Still fine read-only: the RTC sync (sets the in-memory clock and the PiSugar
*hardware* RTC, never the card), the paired speaker (keys are baked into the
frozen image), and the battery guard (reads only). What you give up: persistent
logs — journald resets each boot, so capture logs before rebooting if you're
debugging.
