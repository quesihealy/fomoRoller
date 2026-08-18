#!/bin/bash
# FOMO Roller — reliable Bluetooth speaker connect at boot.
#
# bluez's built-in auto-reconnect for trusted devices is flaky on a cold boot
# (the adapter may be soft-blocked/unpowered, and the speaker may not be ready
# yet). This actively retries until the speaker reports connected, so the
# roller always has an audio sink before playback starts.
#
# Speaker MAC comes from FOMO_BT_MAC (set in the systemd unit). The speaker
# must already be paired + trusted once (see SETUP.md step 5).

set -u

MAC="${FOMO_BT_MAC:?set FOMO_BT_MAC}"
TRIES=40          # up to TRIES * SLEEP seconds to get connected
SLEEP=3

# Bluetooth can come up soft-blocked and powered off after a fresh boot.
rfkill unblock bluetooth 2>/dev/null || true
bluetoothctl power on >/dev/null 2>&1 || true

for i in $(seq 1 "$TRIES"); do
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; then
        echo "Speaker $MAC connected (attempt $i)"
        exit 0
    fi
    bluetoothctl connect "$MAC" >/dev/null 2>&1 || true
    sleep "$SLEEP"
done

echo "Could not connect speaker $MAC after $((TRIES * SLEEP))s" >&2
exit 1
