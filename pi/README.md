# pi

Code that runs on the Raspberry Pi inside the roller: picks the MP3 for the
current 30-minute slot from the SD card and plays it when rolled.

- `playback.py` — motion-triggered playback (MPU-6050 + VLC over Bluetooth)
- `service/fomo-roller.service` — systemd unit to run it on boot
- `requirements.txt` — pip deps
- [`SETUP.md`](./SETUP.md) — one-time hardware/OS setup for a fresh Pi
