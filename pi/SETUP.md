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

**Address conflict to avoid:** the PiSugar's I2C address is configurable
between `0x57` and `0x68`. The MPU-6050 defaults to `0x68` (`0x69` if `AD0`
is pulled high). Confirm the PiSugar is set to `0x57` — see the [PiSugar 3
I2C datasheet](https://github.com/PiSugar/PiSugar/wiki/PiSugar-3-I2C-Datasheet) —
otherwise the two devices collide on the bus.

Check both are visible and at distinct addresses:

```sh
sudo apt install i2c-tools
i2cdetect -y 1
# expect 0x57 (PiSugar) and 0x68 (MPU-6050)
```

## 4. Make the PiSugar RTC the system clock source

There's no internet on the playa, so the Pi can't get the time from NTP —
and `playback.py` picks which MP3 to play from `datetime.now()`. Without a
working RTC, every power cycle resets the clock and the roller plays the
wrong slot until someone fixes it by hand. The PiSugar's onboard RTC
solves this, but it has to be wired up as the system clock source:

```sh
# add to /boot/firmware/config.txt (or /boot/config.txt on older OS)
dtoverlay=i2c-rtc,<chip>   # see PiSugar 3 docs for the exact chip name
```

Then disable the fake hwclock (it fights with a real one) and confirm the
RTC survives a reboot:

```sh
sudo apt remove fake-hwclock
sudo systemctl disable fake-hwclock
sudo hwclock -w   # write current system time to the RTC once, while online
sudo reboot
hwclock -r        # should show correct time even with Wi-Fi/NTP unavailable
```

Reference: [PiSugar 3 Series wiki](https://github.com/PiSugar/PiSugar/wiki/PiSugar-3-Series).

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
mkdir -p /home/pi/fomo-roller
# copy playback.py, requirements.txt, and service/ here
python3 -m venv /home/pi/fomo-roller/venv
/home/pi/fomo-roller/venv/bin/pip install -r /home/pi/fomo-roller/requirements.txt
```

Update `ExecStart` in `service/fomo-roller.service` to use the venv's
python if you don't install system-wide.

## 7. Copy the audio files

From the Mac, after running the generator:

```sh
rsync -avz --progress audio/ pi@<pi-hostname>.local:/home/pi/audio/
```

## 8. Install the systemd service

```sh
sudo cp service/fomo-roller.service /etc/systemd/system/
sudo nano /etc/systemd/system/fomo-roller.service   # set FOMO_BT_MAC
sudo systemctl daemon-reload
sudo systemctl enable --now fomo-roller
journalctl -u fomo-roller -f   # watch logs
```

## 9. Tune motion detection

`MOTION_THRESHOLD` in `playback.py` is a per-sample delta on the raw
Z-axis accelerometer reading, not an absolute value — it depends on how
the sensor is physically mounted. Roll the actual assembled device and
watch `journalctl -u fomo-roller -f` for false triggers (too low) or
missed rolls (too high), then adjust and restart the service.
