"""Frame selection for the pose-labeling queue (``/app/pose/label``).

Filters to ``prob`` in 0.75-0.90 during Arizona daylight hours specifically
-- see naturalist memory / ``modules/naturalist`` OWLv2 findings: the
hole-cam classifier's highest-confidence band (0.90+) is dominated by a
known night-IR false-positive artifact (an overexposed plant pot, not
Cassini), so pulling from the "highest prob" band would poison the pose
label set with non-Cassini frames.
"""
from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

from app.settings import settings
from shared.db import SessionLocal
from shared.models import hole_camera

_ARIZONA = ZoneInfo("America/Phoenix")  # no DST -- fixed UTC-7 year-round

MIN_PROB = 0.75
MAX_PROB = 0.90
TARGET_COUNT = 200
# hole_camera has ~2 years of history (from 2024-08-07) -- a 90-day lookback
# turned out to only match ~20 rows total, all from one sit-still window, so
# every sampled frame was the same pose (2026-07-19 bug report). 730 days
# gives a genuinely large candidate pool to sample diversity from.
LOOKBACK_DAYS = 730
# Frames within a sitting are ~1-2s apart, so naive sampling of a small
# candidate pool clusters on whatever he happened to be doing in one
# sitting. Require picks to be spaced >= this far apart in time so they land
# in different sittings (different real poses), not just different rows.
MIN_GAP_SECONDS = 10 * 60
# Fixed seed: sample_frames() is called fresh on every request
# (next-frame/progress), and the queue (membership + target count) needs to
# stay stable across those calls within a labeling session -- true
# unseeded randomness would make the pool jitter call to call.
_SEED = 20260718


def _in_daylight(ts_ms: int) -> bool:
    hour = dt.datetime.fromtimestamp(ts_ms / 1000, tz=_ARIZONA).hour
    return settings.shed_scan_daylight_start_hour <= hour < settings.shed_scan_daylight_end_hour


_cache: dict[tuple[int, int], list[str]] = {}


def sample_frames(target: int = TARGET_COUNT, *, lookback_days: int = LOOKBACK_DAYS) -> list[str]:
    """Up to `target` distinct hole-cam frame paths, greedily picked (in a
    deterministically-shuffled order) so no two are within MIN_GAP_SECONDS of
    each other -- spreads the sample across many different sittings/poses
    instead of clumping into whichever sitting the candidate pool happens to
    be dense in.

    Cached per (target, lookback_days) for the life of the process: the
    router calls this on every /next-frame and /progress request, and with
    `lookback_days` now covering nearly all of `hole_camera`'s ~2-year, ~15M
    row history, the underlying query (no index on `prob`) takes ~12s on its
    own -- fine once, not on every click. The queue is deterministic (fixed
    seed) so there's nothing gained by recomputing it live; restart the app
    to pick up new hole_camera data.
    """
    cache_key = (target, lookback_days)
    if cache_key in _cache:
        return _cache[cache_key]

    since_ms = int((dt.datetime.now() - dt.timedelta(days=lookback_days)).timestamp() * 1000)
    session = SessionLocal()
    try:
        rows = (
            session.query(hole_camera.timestamp, hole_camera.path)
            .filter(hole_camera.timestamp >= since_ms)
            .filter(hole_camera.prob >= MIN_PROB, hole_camera.prob <= MAX_PROB)
            .order_by(hole_camera.timestamp)
            .all()
        )
    finally:
        session.close()

    candidates = [(ts, path) for ts, path in rows if path and _in_daylight(ts)]

    shuffled = candidates[:]
    random.Random(_SEED).shuffle(shuffled)

    picked: list[tuple[int, str]] = []
    picked_ts: list[int] = []
    gap_ms = MIN_GAP_SECONDS * 1000
    for ts, path in shuffled:
        if all(abs(ts - t) >= gap_ms for t in picked_ts):
            picked.append((ts, path))
            picked_ts.append(ts)
        if len(picked) >= target:
            break

    picked.sort()  # chronological order once selected, just easier to reason about
    _cache[cache_key] = [path for _, path in picked]
    return _cache[cache_key]


def frame_url(path: str) -> str:
    root = str(settings.hole_cam_dir).rstrip("/") + "/"
    return "/hole-cam/static/" + path[len(root):] if path.startswith(root) else ""
