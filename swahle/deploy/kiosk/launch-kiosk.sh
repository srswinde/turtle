#!/bin/bash
# Kiosk launcher for the Swahle smart mirror (Raspberry Pi, user `scott`).
#
# Runs inside the Pi's existing desktop session (Raspberry Pi OS Bookworm --
# the compositor is labwc as of the 2024/2025 images, was Wayfire before;
# this script doesn't care which). Started from that session's autostart --
# see install.sh. It keeps Chromium up itself, so it works whether or not the
# session provides a respawner.
set -u

URL="${SWAHLE_MIRROR_URL:-http://192.168.0.164:8800}"
PROFILE="${SWAHLE_KIOSK_PROFILE:-/home/scott/.mirror-chromium}"

CHROMIUM="$(command -v chromium || command -v chromium-browser)"
if [ -z "$CHROMIUM" ]; then
  echo "no chromium binary found" >&2
  exit 1
fi

# Wait for the mirror server so a boot race never paints an error page.
until curl -sf --max-time 4 "$URL/healthz" >/dev/null; do
  echo "waiting for $URL ..."
  sleep 3
done

while true; do
  # A reboot / session switch leaves Chromium's single-instance lock behind,
  # and a second launch that sees a stale lock just forwards its URL and exits
  # 0 -- which looks exactly like "kiosk never started". This profile is used
  # only by the kiosk, so clearing the lock unconditionally is always safe.
  rm -f "$PROFILE"/Singleton* 2>/dev/null

  # Keep the previous-exit state clean so a power-cut doesn't show the
  # "restore pages?" bar.
  PREFS="$PROFILE/Default/Preferences"
  if [ -f "$PREFS" ]; then
    sed -i 's/"exit_type":"[^"]*"/"exit_type":"Normal"/;s/"exited_cleanly":false/"exited_cleanly":true/' "$PREFS" 2>/dev/null || true
  fi

  "$CHROMIUM" \
    --kiosk \
    --ozone-platform=wayland \
    --noerrdialogs \
    --disable-infobars \
    --disable-features=Translate \
    --disable-session-crashed-bubble \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --autoplay-policy=no-user-gesture-required \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    --user-data-dir="$PROFILE" \
    "$URL"

  echo "chromium exited ($?); relaunching in 3s"
  sleep 3
done
