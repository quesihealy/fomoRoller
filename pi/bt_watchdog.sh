#!/bin/bash
# FOMO Roller — Bluetooth speaker keep-alive watchdog (runs continuously).
#
# bt_connect.sh gets the speaker connected once at boot and gates playback.
# This watchdog owns everything after that: if the speaker dies mid-session
# (battery flat, powered off, out of range) it reconnects as soon as it's
# back. On a drop->reconnect it restarts the playback service so VLC re-opens
# the bluealsa sink — a running player can't recover a PCM that vanished, so
# without this it would silently play into a dead device after a reconnect.
#
# Speaker MAC comes from FOMO_BT_MAC (set in the systemd unit). The speaker
# must already be paired + trusted once (see SETUP.md step 5).

set -u

MAC="${FOMO_BT_MAC:?set FOMO_BT_MAC}"
POLL="${FOMO_BT_POLL:-5}"          # seconds between connection checks
CONNECT_TIMEOUT=8                  # cap a single connect attempt

is_connected() {
    bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"
}

# Bluetooth can come up soft-blocked/powered off after a fresh boot.
rfkill unblock bluetooth 2>/dev/null || true
bluetoothctl power on >/dev/null 2>&1 || true

was_connected=-1                   # -1 unknown, 0 disconnected, 1 connected
while true; do
    if is_connected; then
        if [ "$was_connected" -eq 0 ]; then
            # disconnected -> connected: bounce playback to re-acquire the sink
            echo "Speaker $MAC reconnected — restarting fomo-roller"
            systemctl restart fomo-roller.service 2>/dev/null || true
        elif [ "$was_connected" -eq -1 ]; then
            echo "Speaker $MAC connected"
        fi
        was_connected=1
    else
        if [ "$was_connected" -eq 1 ]; then
            echo "Speaker $MAC dropped — reconnecting..."
        fi
        was_connected=0
        bluetoothctl power on >/dev/null 2>&1 || true
        timeout "$CONNECT_TIMEOUT" bluetoothctl connect "$MAC" >/dev/null 2>&1 || true
    fi
    sleep "$POLL"
done
