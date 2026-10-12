#!/bin/bash
# One-time kiosk install on the Pi (user `scott`). Idempotent -- safe to re-run.
#
# Installs the launcher under ~/.local/bin and hooks it into the desktop
# session's autostart. Raspberry Pi OS Bookworm ships two Wayland sessions
# (labwc -- the current default -- and Wayfire); this wires up whichever one(s)
# are present so the kiosk survives a compositor switch. Does not touch
# anything running right now -- it takes effect on the next login/reboot.
set -eu

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/swahle-mirror-kiosk.sh"
POWER_BIN="$HOME/.local/bin/swahle-mirror-display-power.sh"

mkdir -p "$(dirname "$BIN")"
cp "$DIR/launch-kiosk.sh" "$BIN"
chmod +x "$BIN"
echo "installed $BIN"

cp "$DIR/display-power.sh" "$POWER_BIN"
chmod +x "$POWER_BIN"
echo "installed $POWER_BIN"

# --- labwc (current RPi default) -------------------------------------------
# labwc runs the *user* autostart INSTEAD OF the system one, so seed it from
# the system default (panel, desktop icons, ...) before appending our line.
LABWC="$HOME/.config/labwc/autostart"
mkdir -p "$(dirname "$LABWC")"
if [ ! -f "$LABWC" ]; then
  if [ -f /etc/xdg/labwc/autostart ]; then
    cp /etc/xdg/labwc/autostart "$LABWC"
    echo "seeded $LABWC from /etc/xdg/labwc/autostart"
  else
    : > "$LABWC"
  fi
fi
if grep -q "swahle-mirror-kiosk" "$LABWC"; then
  echo "labwc autostart already has the kiosk line"
else
  printf '%s &\n' "$BIN" >> "$LABWC"
  echo "added kiosk to $LABWC"
fi
if grep -q "swahle-mirror-display-power" "$LABWC"; then
  echo "labwc autostart already has the display-power line"
else
  printf '%s &\n' "$POWER_BIN" >> "$LABWC"
  echo "added display-power to $LABWC"
fi

# --- Wayfire (fallback, if they switch back) ------------------------------
WAYFIRE="$HOME/.config/wayfire.ini"
if [ -f "$WAYFIRE" ]; then
  if grep -q "swahle_mirror " "$WAYFIRE"; then
    echo "wayfire.ini already has the kiosk line"
  elif grep -q "^\[autostart\]" "$WAYFIRE"; then
    sed -i "/^\[autostart\]/a swahle_mirror = $BIN" "$WAYFIRE"
    echo "added kiosk to existing [autostart] in wayfire.ini"
  else
    printf '\n[autostart]\nswahle_mirror = %s\n' "$BIN" >> "$WAYFIRE"
    echo "added [autostart] section to wayfire.ini"
  fi
  if grep -q "swahle_mirror_display_power" "$WAYFIRE"; then
    echo "wayfire.ini already has the display-power line"
  elif grep -q "^\[autostart\]" "$WAYFIRE"; then
    sed -i "/^\[autostart\]/a swahle_mirror_display_power = $POWER_BIN" "$WAYFIRE"
    echo "added display-power to existing [autostart] in wayfire.ini"
  else
    printf '\n[autostart]\nswahle_mirror_display_power = %s\n' "$POWER_BIN" >> "$WAYFIRE"
    echo "added [autostart] section to wayfire.ini"
  fi
fi

echo
echo "done -- takes effect on the next login/reboot."
echo "try it now without rebooting:  $BIN &"
