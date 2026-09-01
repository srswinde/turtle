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

## Screen blanking

Not observed as an issue during setup (the current manually-launched Chromium
ran for 2+ days without blanking), so nothing was changed. If the panel does
start sleeping: `wlopm` and `wlr-randr` are both already on this image and can
force outputs on, or the idle timeout can be tuned in `wayfire.ini`.

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
