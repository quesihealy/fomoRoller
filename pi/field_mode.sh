#!/bin/bash
# FOMO Roller — field power diet.
#
# The playa build runs fully offline, so the WiFi radio is dead weight. This
# turns WiFi and the two status LEDs off to save battery (and a little heat).
# Bluetooth stays on — it's the audio path. Toggles a marked block in the boot
# config, so it's fully reversible.
#
# Usage:
#   field_mode.sh on    # add the diet lines, then reboot
#   field_mode.sh off   # remove them (reboot to apply)
#
# WARNING: 'on' disables WiFi — you WILL lose SSH after the reboot. Do it last,
# once you no longer need remote access. To undo without SSH: run 'off' from a
# local console (keyboard + monitor), or edit the boot config on the SD card
# from any computer (it's the FAT boot partition) and delete the marked block.
#
# Run BEFORE enabling the overlay filesystem (SETUP step 11) — the boot config
# must be writable.

set -euo pipefail

CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt

BEGIN="# fomo-field-diet-begin"
END="# fomo-field-diet-end"

strip() { sudo sed -i "/$BEGIN/,/$END/d" "$CONFIG"; }

case "${1:-}" in
  on)
    strip                                   # avoid duplicate blocks
    sudo tee -a "$CONFIG" >/dev/null <<EOF
$BEGIN
dtoverlay=disable-wifi
dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
dtparam=pwr_led_trigger=none
dtparam=pwr_led_activelow=off
$END
EOF
    echo "Field power diet ON (WiFi + LEDs off) in $CONFIG. Rebooting — SSH will drop."
    sudo reboot
    ;;
  off)
    strip
    echo "Field power diet OFF (WiFi + LEDs restored) in $CONFIG. Reboot to apply."
    ;;
  *)
    echo "usage: $0 on|off"; exit 2 ;;
esac
