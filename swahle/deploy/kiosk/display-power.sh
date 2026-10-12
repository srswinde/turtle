#!/bin/bash
# Display power management for the Swahle smart mirror kiosk (Raspberry Pi).
#
# The mirror's quiet-hours dimming (app.css / app.js `applyBrightness`) draws
# the page black with `filter: brightness(0)`, but that's still a lit
# backlight showing black pixels -- visible as a dull glow in a dark room.
# This instead actually turns the panel off during quiet hours, using the
# same `/mirror/api/ambient` `quiet` payload the page itself polls, so the
# schedule (incl. "until sunrise") only lives in one place: ambient.py.
#
# How the panel is powered off: NOT the compositor (`wlopm`/`wlr-randr
# --off`, the wlr-output-power-management / wlr-output-management
# protocols) -- tried first, but labwc re-enables the output on its own
# within ~5-20s of an external off request (confirmed by watching `wlr-randr`
# / `wlopm`'s own reported state flip back to "on" with nothing else
# touching it), so the panel never stayed dark. Instead this talks straight
# to the monitor over DDC/CI (`ddcutil`, VCP feature 0xD6 "power mode") on
# its I2C bus -- the Pi exposes this as /dev/i2c-20 for the HDMI0 port
# (`ddcutil detect`), and `scott` is already in the `i2c` group so no sudo is
# needed. This is a command to the monitor's own firmware, independent of
# the HDMI signal and the compositor, so it holds regardless of what
# Chromium keeps rendering -- confirmed stable 40+s in testing, vs. the
# compositor approach's ~5-20s.
#
# A touch during quiet hours still needs to wake the panel -- the web page's
# own wake-on-touch (quiet.wake_seconds) only un-dims pixels that are already
# invisible because the monitor itself is off, so this watches the touch
# overlay device directly (a separate USB part, unaffected by the monitor's
# power state) and un-sleeps it the same way.
#
# The menu's manual "Screen Off" button works the same way: it just sets
# `screen_off` in /mirror/api/ambient (see modules/mirror/state.py), which
# this treats as another reason to power off, on top of quiet hours. A touch
# clears it server-side (POST /mirror/api/display/on) the moment it wakes the
# panel, so it doesn't stay manually-off forever once the user's back.
#
# Runs inside the Pi's desktop session (started from autostart, same as
# launch-kiosk.sh -- see install.sh) -- not that it matters for ddcutil
# itself (no Wayland/X session needed), but the touch watcher does need the
# session's device permissions.
set -u

URL="${SWAHLE_MIRROR_URL:-http://192.168.0.164:8800}"
DDC_DISPLAY="${SWAHLE_MIRROR_DDC_DISPLAY:-1}"   # ddcutil's --display index, see `ddcutil detect`
TOUCH_DEVICE="${SWAHLE_MIRROR_TOUCH_DEVICE:-/dev/input/by-id/usb-Multi_touch_Multi_touch_overlay_device_818390D11839-event-if01}"
POLL_SECONDS=20

command -v ddcutil >/dev/null || { echo "ddcutil not found (apt install ddcutil)" >&2; exit 1; }

state="on"   # last power mode we told the monitor, so we don't spam it every poll

set_display() {
  [ "$state" = "$1" ] && return
  if [ "$1" = "off" ]; then
    ddcutil --display "$DDC_DISPLAY" setvcp 0xD6 0x05 2>/dev/null   # "turn off display"
  else
    ddcutil --display "$DDC_DISPLAY" setvcp 0xD6 0x01 2>/dev/null   # "on"
  fi
  state="$1"
  echo "$(date '+%F %T') display -> $1"
}

# Touch watcher: any event on the overlay bumps this stamp file's mtime.
# Runs for the life of this script -- independent of the output's power
# state, so a touch still registers while the panel is off.
TOUCH_STAMP="$(mktemp)"
if [ -e "$TOUCH_DEVICE" ]; then
  ( libinput debug-events --device "$TOUCH_DEVICE" 2>/dev/null | \
      while read -r _; do touch "$TOUCH_STAMP"; done ) &
  WATCHER_PID=$!
else
  echo "touch device $TOUCH_DEVICE not found -- wake-on-touch disabled" >&2
  WATCHER_PID=""
fi
cleanup() { [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null; rm -f "$TOUCH_STAMP"; }
trap cleanup EXIT

prev_manual_off="0"
manual_off_since=0   # epoch seconds manual_off last turned on -- see below

while true; do
  payload="$(curl -sf --max-time 5 "$URL/mirror/api/ambient" 2>/dev/null)"
  active="" manual_off="0" wake_s=30
  if [ -n "$payload" ]; then
    read -r active wake_s manual_off <<<"$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    q = d.get("quiet") or {}
    print(("1" if q.get("active") else "0"), int(q.get("wake_seconds") or 30),
          "1" if d.get("screen_off") else "0")
except Exception:
    print("", 30, "0")
')"
  fi

  now=$(date +%s)
  touch_ts=$(stat -c %Y "$TOUCH_STAMP" 2>/dev/null || echo 0)

  # The tap on the menu's "Screen Off" button is itself a touch -- it would
  # immediately satisfy a naive "touched recently" wake check and clear the
  # flag right back off. So manual_off only wakes on a touch strictly after
  # the moment we first saw it turn on, not on "touched in the last N sec".
  if [ "$manual_off" = "1" ] && [ "$prev_manual_off" != "1" ]; then
    manual_off_since=$now
  fi
  prev_manual_off="$manual_off"

  if [ "$manual_off" = "1" ]; then
    # Manual off is its own authority while set -- no grace-window
    # re-blanking, just off until a touch strictly after it was requested.
    if [ "$touch_ts" -gt "$manual_off_since" ]; then
      set_display on
      curl -sf --max-time 5 -X POST "$URL/mirror/api/display/on" >/dev/null 2>&1
    else
      set_display off
    fi
  elif [ "$active" = "1" ]; then
    touch_age=$(( now - touch_ts ))
    if [ "$touch_age" -lt "$wake_s" ]; then
      set_display on
    else
      set_display off
    fi
  else
    # Not in quiet hours, no manual off, or the backend didn't answer -- fail
    # safe to on rather than risk a stuck-dark panel with no way to tell why.
    set_display on
  fi

  sleep "$POLL_SECONDS"
done
