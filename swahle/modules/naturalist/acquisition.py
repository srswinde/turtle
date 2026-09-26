"""Phase 2: auto-analyze hole-cam departures (exit direction, via Claude) and
continuously scan the shed cam with OWLv2 for real sightings.

Both watchers run as background asyncio tasks inside the web app (mirrors
dashboard.pump_alert), not a separate process — everything they need (DB,
Claude API, ml_service HTTP) is already reachable from there.

The shed-cam scan (``ShedScanner``) runs unconditionally through daylight
hours — it used to only fire after a hole-cam departure classified
``toward_shed`` by Claude, but that made the whole detection pipeline
dependent on one LLM classification being right. Confirmed wrong in practice
2026-07-16: a departure classified ``away-from-camera`` never triggered a
scan, yet Cassini was directly observed at the shed that day. The departure
classification (``cassini_departure.toward_shed``) is kept for narrative
purposes only now — it no longer gates anything.

Phase 2.5 (2026-07-27) adds active PTZ steering: once OWLv2 confirms a hit at
the shed cam's static resting view, ``ShedScanner`` pans/tilts through
``settings.shed_scan_pantilt_grid`` looking for him in the wider yard,
switching between three states:

- ``idle``    — watching the resting-view frame (original v2 behavior).
- ``scanning``— sweeping the grid looking for a hit, looping indefinitely
  until either found or the hole cam confirms he's back home.
- ``tracking``— parked on the grid position where he was found, re-checking
  OWLv2 there until he's out of frame, then back to ``scanning``.

Phase 3 (2026-08-23) adds ``SessionVerifier``: an OWLv2 second opinion on each
hole-cam session, to catch VGG classifier false positives (shadow/light
flicker on the empty hole interior) the frame/duration persistence gate in
``sessionize.py`` doesn't. Flag-only for now -- see
``settings.hole_scan_verify_threshold``.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

import httpx
from starlette.concurrency import run_in_threadpool

from app.settings import settings
from shared.db import SessionLocal
from shared.events import TAKEOVER, bus, make_event
from shared.models import HAS_TURTLE, hole_camera, shed_camera

from .models import (
    get_departure,
    get_last_shed_detection_created,
    get_session_check,
    save_departure,
    save_session_check,
    save_shed_detection,
)
from .sessionize import MIN_PROB, departure_frames, peak_frame_path, recent_sessions
from .vision import VisionError, analyze_departure

log = logging.getLogger("swahle.naturalist.acquisition")

_ARIZONA = ZoneInfo("America/Phoenix")  # no DST -- fixed UTC-7 year-round

_shed_camera_obj = None  # lazy Camera() — amcrest construction is lazy/non-blocking (see camera.py)


def _get_shed_camera():
    global _shed_camera_obj
    if _shed_camera_obj is None:
        from modules.cameras.camera import Camera
        _shed_camera_obj = Camera(settings.cameras["shed"])
    return _shed_camera_obj


def _toward_shed(direction: str) -> int | None:
    if not direction:
        return None
    unreachable = {d.strip() for d in settings.shed_unreachable_directions.split(",") if d.strip()}
    return 0 if direction in unreachable else 1


def _latest_shed_frame() -> tuple[int, str] | None:
    session = SessionLocal()
    try:
        row = (
            session.query(shed_camera.timestamp, shed_camera.path)
            .order_by(shed_camera.timestamp.desc())
            .first()
        )
    finally:
        session.close()
    return (row.timestamp, row.path) if row and row.path else None


def _read_ptz_position() -> tuple[float | None, float | None]:
    """Best-effort read of the shed-cam's current pan/tilt (builds a dataset for
    a possible future Phase 2.5 without gating anything in v1 on it working)."""
    status = _get_shed_camera().amcrest.ptz_status()
    pan = tilt = None
    for line in status.splitlines():
        if line.startswith("status.Postion[0]"):
            pan = float(line.split("=", 1)[1])
        elif line.startswith("status.Postion[1]"):
            tilt = float(line.split("=", 1)[1])
    return pan, tilt


def _persist_scan_frame(data: bytes, ts: dt.datetime) -> str:
    """Write a scan frame (only ever called once a hit is confirmed) under
    ``shed_cam_dir`` so ``save_shed_detection``/the UI have a servable path.
    Reuses the cameras module's dir-perms helpers so the file is readable by
    the mirror service (see camera.py:_mkdirs_readable)."""
    from modules.cameras.camera import _chmod_readable, _mkdirs_readable

    base = settings.shed_cam_dir
    p = base / f"{ts.year}/{ts.month}/{ts.day}/mtxscan_{ts.timestamp()}.jpg"
    _mkdirs_readable(p.parent, base)
    p.write_bytes(data)
    _chmod_readable(p)
    return str(p)


async def _detect_tortoise(image_bytes: bytes, prompts: list[str] | None = None) -> dict | None:
    if not settings.ml_service_url:
        return None
    body = {
        "image": base64.b64encode(image_bytes).decode("ascii"),
        "prompts": prompts or settings.shed_scan_prompts,
        "threshold": 0.05,  # broad net; the caller's own threshold decides a real hit
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(settings.ml_service_url.rstrip("/") + "/detect/locate",
                                 json=body)
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    return hits[0] if hits else None


def _cassini_confirmed_home(within_seconds: float) -> bool:
    """Has the hole cam seen him at high confidence in the last ``within_seconds``?
    Used to end a yard scan/track — he can't be in the yard and at the hole at
    once, so a fresh hole-cam presence hit is the signal to stop searching."""
    cutoff_ms = int(time.time() * 1000) - int(within_seconds * 1000)
    session = SessionLocal()
    try:
        row = (
            session.query(hole_camera.timestamp)
            .filter(hole_camera.timestamp >= cutoff_ms)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .filter(hole_camera.prob > MIN_PROB)
            .order_by(hole_camera.timestamp.desc())
            .first()
        )
    finally:
        session.close()
    return row is not None


async def _notify_sighting(ts: int) -> None:
    if not settings.notify_enabled or not settings.notify_ifttt_url:
        return
    when = dt.datetime.fromtimestamp(ts / 1000, tz=_ARIZONA).strftime("%-I:%M %p")
    message = f"Cassini spotted at the shed cam at {when}"
    url = settings.notify_site_base_url.rstrip("/") + "/app/naturalist"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(settings.notify_ifttt_url, json={"message": message, "url": url})
        log.info("Sent shed-cam sighting notification for %s", when)
    except httpx.HTTPError as exc:
        log.warning("shed-cam IFTTT notify failed: %s", exc)


def _az_clock(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=_ARIZONA).strftime("%-I:%M %p")


async def _mirror_shed_sighting(ts_ms: int, path: str) -> None:
    """Light up the smart mirror with the shed-cam catch. Best-effort: the DB
    log cooldown in the callers is the debounce, and ``bus.publish`` swallows a
    Redis outage (no mirror running is the normal case)."""
    await bus.publish(make_event(
        type="shed_sighting", kind=TAKEOVER, icon="\U0001F422",
        title="Cassini at the shed", subtitle=_az_clock(ts_ms),
        image={"kind": "shed", "ref": path}, ts=ts_ms,
    ))


def _latest_hole_presence() -> tuple[int, str] | None:
    """Most recent hole-cam frame that counts as Cassini being home *now*."""
    cutoff_ms = int(time.time() * 1000) - settings.mirror_hole_fresh_seconds * 1000
    session = SessionLocal()
    try:
        row = (
            session.query(hole_camera.timestamp, hole_camera.path)
            .filter(hole_camera.timestamp >= cutoff_ms)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .filter(hole_camera.prob > MIN_PROB)
            .order_by(hole_camera.timestamp.desc())
            .first()
        )
    finally:
        session.close()
    return (row.timestamp, row.path) if row and row.path else None


class MirrorHolePresence:
    """Emits a ``hole_sighting`` mirror takeover when Cassini turns up live at
    the hole cam, debounced to one event per visit. Independent of
    SessionVerifier/DepartureWatcher (those fire ~minutes after a session
    *ends*; the mirror wants the arrival, promptly)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._last_frame_ms = 0
        self._last_emit = 0.0

    async def start_poll(self) -> None:
        if not settings.mirror_producers_enabled:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="mirror-hole-presence")

    async def stop_poll(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 - keep the poller alive
                log.exception("mirror hole-presence tick failed")
            await asyncio.sleep(settings.mirror_hole_poll_seconds)

    async def _tick(self) -> None:
        latest = await run_in_threadpool(_latest_hole_presence)
        if not latest:
            return
        ts, path = latest
        if ts <= self._last_frame_ms:
            return
        self._last_frame_ms = ts
        now = time.time()
        if now - self._last_emit < settings.mirror_hole_cooldown_seconds:
            return
        self._last_emit = now
        await bus.publish(make_event(
            type="hole_sighting", kind=TAKEOVER, icon="\U0001F422",
            title="Cassini at the hole", subtitle=_az_clock(ts),
            image={"kind": "hole", "ref": path}, ts=ts,
        ))
        log.info("mirror: emitted hole_sighting for %s", _az_clock(ts))


class DepartureWatcher:
    """Background poller: auto-analyzes newly-ended hole-cam departures with
    Claude to say which way he exited. Purely narrative now -- see
    ``ShedScanner`` for shed-cam detection, which runs unconditionally and no
    longer depends on this classification."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start_poll(self) -> None:
        if not settings.naturalist_auto_analyze:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="naturalist-departure-watcher")

    async def stop_poll(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._check_once()
            except Exception:  # noqa: BLE001 - keep the poller alive across errors
                log.exception("departure watcher tick failed")
            await asyncio.sleep(settings.naturalist_poll_seconds)

    async def _check_once(self) -> None:
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - int(settings.naturalist_lookback_seconds * 1000)
        sessions = await run_in_threadpool(recent_sessions, since_ms)
        ready_cutoff_ms = settings.naturalist_departure_ready_seconds * 1000

        for s in sessions:
            if now_ms - s["end"] < ready_cutoff_ms:
                continue  # not safely ended yet — could still be an ongoing session
            existing = await run_in_threadpool(get_departure, s["end"])
            if existing is not None:
                continue  # already analyzed
            await self._analyze_and_maybe_acquire(s)

    async def _analyze_and_maybe_acquire(self, s: dict) -> None:
        frames = await run_in_threadpool(
            departure_frames, s["end"], s["start"],
            count=settings.departure_frames,
            pre_seconds=settings.departure_pre_seconds,
            post_seconds=settings.departure_post_seconds,
            max_px=settings.departure_max_px,
        )
        if not frames:
            log.warning("no frames found for departure %s; skipping auto-analysis", s["end"])
            return
        try:
            analysis = await analyze_departure(frames)
        except VisionError as exc:
            log.warning("auto-analysis failed for departure %s: %s", s["end"], exc)
            return

        toward_shed = _toward_shed(analysis.get("direction", ""))
        await run_in_threadpool(
            save_departure, session_end=s["end"], session_start=s["start"],
            analysis=analysis, toward_shed=toward_shed,
            model_name=settings.anthropic_model, frames_used=len(frames),
        )
        log.info("auto-analyzed departure %s -> %s (toward_shed=%s, narrative only)",
                 s["end"], analysis.get("direction"), toward_shed)


class ShedScanner:
    """Background poller: continuously scans the shed cam with OWLv2 through
    Arizona daylight hours, independent of hole-cam departures entirely.
    Also owns the Phase 2.5 pan/tilt yard search once a resting-view hit
    fires (see module docstring for the idle/scanning/tracking states)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._last_notified = 0.0
        self._state = "idle"
        self._home_pan: float | None = None
        self._home_tilt: float | None = None
        self._scan_index = 0
        self._search_started = 0.0
        self._track_pan: float | None = None
        self._track_tilt: float | None = None

    @property
    def state(self) -> str:
        return self._state

    async def start_poll(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="naturalist-shed-scanner")

    async def stop_poll(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _in_daylight_window(self) -> bool:
        hour = dt.datetime.now(_ARIZONA).hour
        return settings.shed_scan_daylight_start_hour <= hour < settings.shed_scan_daylight_end_hour

    async def _loop(self) -> None:
        last_ts_seen = -1
        while True:
            try:
                if self._state == "scanning":
                    await self._scan_step()
                elif self._state == "tracking":
                    await self._track_step()
                elif self._in_daylight_window():
                    last_ts_seen = await self._tick(last_ts_seen)
                    if self._state == "idle":
                        await asyncio.sleep(settings.shed_scan_poll_seconds)
                else:
                    await asyncio.sleep(300.0)  # outside daylight hours: check back in 5 min
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the scanner alive across errors
                log.exception("shed scanner tick failed")
                await asyncio.sleep(settings.shed_scan_poll_seconds)

    async def _grab_scan_bytes(self) -> bytes | None:
        """One shed-cam frame for OWLv2, pulled from the MediaMTX republish so
        MediaMTX holds the only upstream connection to the camera. Returns None
        on any grab failure -- caller just retries on the next tick."""
        from shared.frames import FrameGrabError, grab_jpeg

        try:
            return await grab_jpeg(settings.mediamtx_scan_path,
                                   timeout=settings.frame_grab_timeout)
        except FrameGrabError as exc:
            log.warning("mediamtx scan-frame grab failed: %s", exc)
            return None

    async def _tick(self, last_ts_seen: int) -> int:
        # 2026-09-11: deliberately NOT a settings.shed_scan_frame_source branch.
        # This is the idle poll -- it ran continuously against MediaMTX for one
        # prototype and that alone (a permanent 1080p RTSP tenant, on top of
        # swahle-shed-cam.service's existing snapshot.cgi polling) tipped the
        # camera into a reboot loop within 40 minutes, unrelated to the yard
        # search. Idle stays on the free ride: read whatever
        # swahle-shed-cam.service already captured off disk, zero extra load
        # on the camera. Only the yard search (scanning/tracking, below) reads
        # the setting -- there the frame source *replaces* an existing
        # snapshot.cgi call instead of adding a new standing connection.
        latest = await run_in_threadpool(_latest_shed_frame)
        if not latest or latest[0] == last_ts_seen:
            return last_ts_seen
        ts, path = latest
        try:
            with open(path, "rb") as f:
                image_bytes = f.read()
        except OSError as exc:
            log.warning("shed frame unreadable %s: %s", path, exc)
            return ts

        try:
            hit = await _detect_tortoise(image_bytes)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("OWLv2 call failed during shed scan: %s", exc)
            return ts

        if not hit or hit["score"] <= settings.shed_scan_hit_threshold:
            return ts

        last_created = await run_in_threadpool(get_last_shed_detection_created)
        now_ms = int(time.time() * 1000)
        if last_created is not None and now_ms - last_created < settings.shed_scan_hit_cooldown_seconds * 1000:
            return ts  # still within cooldown of the last logged hit -- he's probably just lingering

        pan = tilt = None
        try:
            pan, tilt = await run_in_threadpool(_read_ptz_position)
        except Exception as exc:  # noqa: BLE001 - best-effort only
            log.debug("ptz_status read failed (non-fatal): %s", exc)

        await run_in_threadpool(
            save_shed_detection, ts=ts, score=hit["score"], path=path,
            box=hit["box"], pan=pan, tilt=tilt,
        )
        log.info("shed detection logged: score=%.3f ts=%s", hit["score"], ts)
        await _mirror_shed_sighting(ts, path)

        now = time.time()
        if now - self._last_notified >= settings.notify_wait_seconds:
            self._last_notified = now
            await _notify_sighting(ts)

        if settings.shed_scan_pantilt_enabled:
            # Fixed config value, not the just-read `pan, tilt` -- returning to
            # wherever the camera happened to be sitting would perpetuate any
            # prior drift instead of always resetting to the known-good spot.
            self._home_pan = settings.shed_scan_home_pan
            self._home_tilt = settings.shed_scan_home_tilt
            self._scan_index = 0
            self._search_started = now
            self._state = "scanning"
            log.info("shed scan: confirmed hit at resting view, starting yard search (home=%s,%s)",
                     self._home_pan, self._home_tilt)

        return ts

    async def _scan_step(self) -> None:
        """One step of the ``scanning`` state: check if he's confirmed home
        yet, else move to the next grid position and look for him there."""
        if await run_in_threadpool(_cassini_confirmed_home, settings.shed_scan_pantilt_home_confirm_seconds):
            log.info("shed scan: Cassini confirmed home via hole cam, ending yard search")
            await self._return_home()
            return

        max_seconds = settings.shed_scan_pantilt_max_search_seconds
        if max_seconds and time.time() - self._search_started > max_seconds:
            log.warning("shed scan: gave up after %.0fs with no hole-cam home confirmation, "
                        "returning to resting position", max_seconds)
            await self._return_home()
            return

        grid = settings.shed_scan_pantilt_grid
        if not grid:
            await self._return_home()
            return
        pan, tilt = grid[self._scan_index % len(grid)]
        self._scan_index += 1

        # PTZ always goes straight to the camera -- MediaMTX only republishes
        # video, it can't relay ONVIF move commands. This is a control call,
        # not frame polling, so it's not the connection-pressure problem the
        # mediamtx frame source addresses.
        camera = _get_shed_camera()
        try:
            await run_in_threadpool(camera.goto_pantilt, pan, tilt)
        except RuntimeError as exc:
            log.warning("shed scan: pan/tilt move to (%s,%s) failed: %s", pan, tilt, exc)
            return

        hit = await self._check_position(pan, tilt)
        if hit is not None:
            self._track_pan, self._track_tilt = pan, tilt
            self._state = "tracking"

    async def _track_step(self) -> None:
        """One step of the ``tracking`` state: stay parked on the position
        where he was found, until he's out of frame or confirmed home."""
        if await run_in_threadpool(_cassini_confirmed_home, settings.shed_scan_pantilt_home_confirm_seconds):
            log.info("shed scan: Cassini confirmed home via hole cam, ending yard search")
            await self._return_home()
            return

        hit = await self._check_position(self._track_pan, self._track_tilt)
        if hit is not None:
            await asyncio.sleep(settings.shed_scan_pantilt_track_poll_seconds)
            return  # still in frame -- keep watching

        log.info("shed scan: Cassini left the tracked view (pan=%s tilt=%s), resuming search",
                 self._track_pan, self._track_tilt)
        self._track_pan = self._track_tilt = None
        self._scan_index = 0
        self._state = "scanning"

    async def _check_position(self, pan: float, tilt: float) -> dict | None:
        """Capture+OWLv2 the shed cam's current view; log+notify a hit. Returns
        the hit dict (or None) so callers can decide the next state."""
        # PTZ was moved on the camera directly just above (ONVIF can't ride
        # RTSP); in mediamtx mode give the republish a beat to catch up to the
        # new view before we grab, so OWLv2 doesn't score the old position.
        if settings.shed_scan_frame_source == "mediamtx":
            await asyncio.sleep(2.0)
            data = await self._grab_scan_bytes()
            if data is None:
                log.warning("shed scan: no mediamtx frame at (%s,%s)", pan, tilt)
                return None
            ts_dt = dt.datetime.now()
            sw = None
        else:
            sw = await run_in_threadpool(_get_shed_camera().take_image, "yardscan_", None, False)
            if not sw:
                log.warning("shed scan: could not capture frame at (%s,%s)", pan, tilt)
                return None
            data, ts_dt = sw.data, sw.timestamp

        try:
            hit = await _detect_tortoise(data)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("OWLv2 call failed during yard scan at (%s,%s): %s", pan, tilt, exc)
            return None

        if not hit or hit["score"] <= settings.shed_scan_hit_threshold:
            return None

        if sw is not None:
            await run_in_threadpool(sw.save_compress, 2, 90)
            path = str(sw.path)
        else:
            path = await run_in_threadpool(_persist_scan_frame, data, ts_dt)
        ts_ms = int(ts_dt.timestamp() * 1000)
        await run_in_threadpool(
            save_shed_detection, ts=ts_ms, score=hit["score"], path=path,
            box=hit["box"], pan=pan, tilt=tilt,
        )
        log.info("yard scan: found Cassini at pan=%s tilt=%s score=%.3f", pan, tilt, hit["score"])
        await _mirror_shed_sighting(ts_ms, path)

        now = time.time()
        if now - self._last_notified >= settings.notify_wait_seconds:
            self._last_notified = now
            await _notify_sighting(ts_ms)

        return hit

    async def _return_home(self) -> None:
        if self._home_pan is not None and self._home_tilt is not None:
            camera = _get_shed_camera()
            try:
                await run_in_threadpool(camera.goto_pantilt, self._home_pan, self._home_tilt)
            except RuntimeError as exc:
                log.warning("shed scan: failed returning to resting position: %s", exc)
        self._state = "idle"
        self._home_pan = self._home_tilt = None
        self._track_pan = self._track_tilt = None


class SessionVerifier:
    """Background poller: OWLv2 second-opinion check on each newly-ended
    hole-cam session, to catch VGG classifier false positives the persistence
    gate alone doesn't (see sessionize.MIN_SESSION_FRAMES/MIN_SESSION_DURATION_S
    docstring). Verification is cached in cassini_session_check and currently
    *flag-only* -- see settings.hole_scan_verify_threshold for why filtering
    isn't wired in yet. Same "settled session" readiness window as
    DepartureWatcher, so a still-growing session isn't checked prematurely."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start_poll(self) -> None:
        if not settings.hole_scan_verify_enabled:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="naturalist-session-verifier")

    async def stop_poll(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._check_once()
            except Exception:  # noqa: BLE001 - keep the poller alive across errors
                log.exception("session verifier tick failed")
            await asyncio.sleep(settings.hole_scan_verify_poll_seconds)

    async def _check_once(self) -> None:
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - int(settings.naturalist_lookback_seconds * 1000)
        sessions = await run_in_threadpool(recent_sessions, since_ms)
        ready_cutoff_ms = settings.naturalist_departure_ready_seconds * 1000

        for s in sessions:
            if now_ms - s["end"] < ready_cutoff_ms:
                continue  # not safely ended yet — could still be an ongoing session
            existing = await run_in_threadpool(get_session_check, s["end"])
            if existing is not None:
                continue  # already verified
            await self._verify(s)

    async def _verify(self, s: dict) -> None:
        peak = await run_in_threadpool(peak_frame_path, s["start"], s["end"])
        if not peak:
            log.warning("session verify: no frame found for session %s; skipping", s["end"])
            return
        try:
            with open(peak, "rb") as f:
                image_bytes = f.read()
        except OSError as exc:
            log.warning("session verify: frame unreadable %s: %s", peak, exc)
            return

        try:
            hit = await _detect_tortoise(image_bytes, prompts=settings.hole_scan_prompts)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("OWLv2 call failed during session verify: %s", exc)
            return  # leave unverified -- retried next tick

        if not settings.ml_service_url:
            verified = None  # unknown, not rejected -- don't flag on a misconfigured/absent service
        else:
            verified = int(bool(hit and hit["score"] > settings.hole_scan_verify_threshold))

        await run_in_threadpool(
            save_session_check, session_end=s["end"], session_start=s["start"],
            verified=verified, score=(hit["score"] if hit else None),
            checked_path=peak, box=(hit["box"] if hit else None),
        )
        log.info("session verify: session %s -> verified=%s score=%s",
                 s["end"], verified, hit["score"] if hit else None)


watcher = DepartureWatcher()
shed_scanner = ShedScanner()
session_verifier = SessionVerifier()
mirror_presence = MirrorHolePresence()
