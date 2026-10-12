# Smart mirror kiosk (Raspberry Pi)

Runs Chromium in kiosk mode on top of the Pi's **existing** LightDM +
Wayfire desktop (Raspberry Pi OS Bookworm, `LXDE-pi-wayfire` session,
autologin as `scott`) — no new compositor, no boot-target change. That
desktop is already proven to work with the touchscreen, so the kiosk just
adds one autostart entry to it.

## Install (idempotent — safe to re-run)

```bash
scp -r deploy/kiosk scott@<pi>:/tmp/swahle-kiosk
ssh scott@<pi> /tmp/swahle-kiosk/install.sh
```

This copies `launch-kiosk.sh` to `~/.local/bin/swahle-mirror-kiosk.sh` and
adds it to `~/.config/wayfire.ini`'s `[autostart]` section. It does **not**
touch anything currently running — it takes effect on the next login/reboot.

To try it immediately without rebooting: `~/.local/bin/swahle-mirror-kiosk.sh &`
(close the existing browser window first so you're not looking at two).

## What the launcher does

`launch-kiosk.sh`:

1. Polls `"$SWAHLE_MIRROR_URL/healthz"` (default `http://192.168.0.164:8800`)
   until the mirror app answers, so a boot race never leaves an error page on
   the glass.
2. Rewrites `exit_type` in the Chromium profile so a power-cut doesn't show
   the "restore pages?" bar.
3. `exec`s `chromium --kiosk …` with a **persistent**
   `--user-data-dir=/home/scott/.mirror-chromium` (not incognito) so the
   `mirror_auth` enrollment cookie survives reboots, plus touch-friendly flags
   (`--disable-pinch`, `--overscroll-history-navigation=0`).

## Enrolling (only if `SWAHLE_MIRROR_TOKEN` is set on the server)

The mirror is LAN-only with no tunnel route, so the token is optional. If you
do set one in `swahle-mirror.service`, enroll the Pi once:

```
http://192.168.0.164:8800/mirror/enroll?token=<the token>
```

That drops a 10-year signed cookie; every other route then requires it.

## Screen blanking / manual "Screen Off"

Chromium itself never blanks -- the panel staying lit all night (as a dim
black glow from `app.css`'s quiet-hours `filter: brightness()`) and the
menu's "Screen Off" button are both handled by `display-power.sh`, installed
alongside the kiosk by `install.sh` as its own autostart line
(`swahle-mirror-display-power.sh`). It polls the same `/mirror/api/ambient`
payload the web page does (`quiet.active` for the nightly schedule,
`screen_off` for the menu button) and, while either is set, talks directly to
the monitor over **DDC/CI** (`ddcutil`, `apt install ddcutil`) to put it in
its own power-off state -- VCP feature `0xD6` ("power mode"), the Pi exposes
this monitor's DDC bus as `/dev/i2c-20` (`ddcutil detect`), and `scott` is
already in the `i2c` group so no sudo is needed.

That's a command to the monitor's firmware, independent of the HDMI signal --
**deliberately not** `wlopm` / `wlr-randr --off` (the obvious first choice):
those ask the *compositor* to power the output off, but labwc here was found
to re-enable it on its own within ~5-20s regardless (confirmed by watching
`wlr-randr`'s own reported state flip back with nothing else touching it),
so the panel never actually stayed dark. DDC/CI holds regardless of what
Chromium keeps rendering -- confirmed stable 40+s in testing.

A touch on the overlay wakes the panel the same way the page's own
wake-on-touch does (watches the touch device directly via
`libinput debug-events` -- a separate USB part, unaffected by the monitor's
power state), then: during quiet hours, lets it go back off after
`mirror_quiet_wake_seconds` of no further touch; for a manual Screen Off, it
clears the flag (`POST /mirror/api/display/on`) and stays on.

If the DDC display index or touch device ever changes (different panel,
different by-id path), override via `SWAHLE_MIRROR_DDC_DISPLAY` /
`SWAHLE_MIRROR_TOUCH_DEVICE` env vars on the autostart line, same pattern as
`SWAHLE_MIRROR_URL` above. Logs go to the same place as the kiosk's -- run it
by hand in a terminal to see its output live.

## Tweaks

- **Different server address:** `SWAHLE_MIRROR_URL=http://<ip>:8800` as an
  env var on the `swahle_mirror` autostart line in `wayfire.ini`, or export it
  before running the script manually.
- **Screen rotation / multiple DSI outputs:** this Pi has both `DSI-1` and
  `DSI-2` configured for touch input in `wayfire.ini` — confirm which output
  the mirror panel actually is before assuming a single-screen layout.
- **Logs:** the autostart entry runs inside the graphical session, so there's
  no separate systemd journal for it; run the script by hand in a terminal
  (`Ctrl+Alt+T` per the wayfire.ini binding) to see its output live.
