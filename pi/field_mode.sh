#!/bin/bash
# FOMO Roller — field power diet.
#
# The playa build runs fully offline, so the WiFi radio is dead weight. This
# disables WiFi to save power. Bluetooth stays on (it's the audio path), and the
# Pi's PWR/ACT status LEDs are deliberately LEFT ON as an on/off indicator —
# they draw negligible power next to the Pi itself, and losing SSH means the
# LEDs are your only at-a-glance "is it alive" signal. Toggles a marked block in
# the boot config, so it's fully reversible.
#
# Usage:
#   field_mode.sh on    # disable WiFi, then reboot
#   field_mode.sh off   # re-enable WiFi (reboot to apply)
#
# WARNING: 'on' disables WiFi — you WILL lose SSH after the reboot. Do it last,
# once you no longer need remote access. To undo without SSH: run 'off' from a
# local console (keyboard + monitor), or edit the boot config on the SD card
# from any computer (it's the FAT boot partition) and delete the marked block.
#
# If root is on a read-only overlay (the freeze, SETUP step 11), the boot
# partition is still writable, so this keeps working.

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
$END
EOF
    echo "Field power diet ON (WiFi off; LEDs kept on) in $CONFIG. Rebooting — SSH will drop."
    sudo reboot
    ;;
  off)
    strip
    echo "Field power diet OFF (WiFi restored) in $CONFIG. Reboot to apply."
    ;;
  *)
    echo "usage: $0 on|off"; exit 2 ;;
esac
