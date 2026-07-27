"""Turn the raw hole-cam Cassini-probability stream into presence *sessions*.

A **session** is a contiguous run of high-confidence Cassini frames; short gaps
(under ``SESSION_GAP_SECONDS``) are bridged so brief occlusions don't split it.
The end of a session is when Cassini left the hole-cam frame; the gap to the next
session is time he spent away. This is the Phase-0 event model, computed purely
from data already in the DB — no torch — so the web container runs it directly.

Later phases persist these as ``perception`` events and enrich them (exit
direction, shed-cam sightings, LLM narration); Phase 0 computes them on the fly.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
from zoneinfo import ZoneInfo

import pandas as pd
from PIL import Image

from app.settings import settings
from shared.db import SessionLocal
from shared.models import HAS_TURTLE, hole_camera

SESSION_GAP_SECONDS = 180   # a longer gap starts a new presence session
MIN_PROB = 0.70             # confidence for a frame to count as "Cassini present"

# Sessions are bucketed by Arizona calendar day (no DST -- fixed UTC-7 year
# round), NOT the server's local time -- swahle-web itself runs in UTC, so a
# naive datetime().timestamp() call would compute "midnight" as UTC midnight
# (5pm the previous evening in Arizona), shifting the whole day window 7h
# early and mislabeling yesterday-evening frames as "today" (see public.py).
_ARIZONA = ZoneInfo("America/Phoenix")


def _path_to_url(path: str | None) -> str:
    """Map a stored hole-cam disk path to its served URL (see public.py)."""
    if not path:
        return ""
    root = str(settings.hole_cam_dir).rstrip("/") + "/"
    return "/hole-cam/static/" + path[len(root):] if path.startswith(root) else ""


def _day_bounds_ms(day: dt.date) -> tuple[int, int]:
    start = dt.datetime(day.year, day.month, day.day, tzinfo=_ARIZONA)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def recent_sessions(since_ms: int, *, min_prob: float = MIN_PROB,
                    gap_seconds: int = SESSION_GAP_SECONDS) -> list[dict]:
    """Presence sessions ending in [since_ms, now) — the rolling-window sibling
    of ``day_sessions`` used by the Phase 2 departure watcher. Same grouping
    logic, no calendar-day framing, no keyframe thumbnails (not needed there).
    """
    session = SessionLocal()
    try:
        q = (
            session.query(hole_camera.timestamp, hole_camera.prob)
            .filter(hole_camera.timestamp >= since_ms)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .order_by(hole_camera.timestamp)
        )
        df = pd.read_sql(q.statement, session.bind)
    finally:
        session.close()

    df = df[df.prob > min_prob].copy()
    if df.empty:
        return []

    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    new_session = df.timestamp.diff().gt(gap_seconds * 1000)
    df["grp"] = new_session.cumsum()

    sessions = []
    for _, g in df.groupby("grp"):
        start_t, end_t = int(g.timestamp.iloc[0]), int(g.timestamp.iloc[-1])
        sessions.append({
            "start": start_t,
            "end": end_t,
            "frames": int(len(g)),
            "peak_prob": round(float(g.prob.max()), 3),
        })
    return sessions


def day_sessions(day: dt.date, *, min_prob: float = MIN_PROB,
                 gap_seconds: int = SESSION_GAP_SECONDS) -> dict:
    """Presence sessions for one local calendar day, plus day totals."""
    start_ms, end_ms = _day_bounds_ms(day)
    session = SessionLocal()
    try:
        q = (
            session.query(hole_camera.timestamp, hole_camera.prob, hole_camera.path)
            .filter(hole_camera.timestamp >= start_ms)
            .filter(hole_camera.timestamp < end_ms)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .order_by(hole_camera.timestamp)
        )
        df = pd.read_sql(q.statement, session.bind)
    finally:
        session.close()

    result: dict = {"date": day.isoformat(), "sessions": [], "totals": {
        "session_count": 0, "present_seconds": 0.0,
        "first_appearance": None, "last_departure": None,
    }}

    if df.empty:
        return result
    df = df[df.prob > min_prob].copy()
    if df.empty:
        return result

    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    new_session = df.timestamp.diff().gt(gap_seconds * 1000)
    df["grp"] = new_session.cumsum()

    sessions = []
    for _, g in df.groupby("grp"):
        peak_idx = g.prob.idxmax()
        start_t, end_t = int(g.timestamp.iloc[0]), int(g.timestamp.iloc[-1])
        sessions.append({
            "start": start_t,
            "end": end_t,
            "duration_s": round((end_t - start_t) / 1000, 1),
            "frames": int(len(g)),
            "peak_prob": round(float(g.prob.max()), 3),
            "mean_prob": round(float(g.prob.mean()), 3),
            "keyframes": {
                "first": _path_to_url(g.path.iloc[0]),
                "peak": _path_to_url(g.loc[peak_idx, "path"]),
                "last": _path_to_url(g.path.iloc[-1]),
            },
        })

    for i in range(1, len(sessions)):
        sessions[i]["away_before_s"] = round(
            (sessions[i]["start"] - sessions[i - 1]["end"]) / 1000, 1)

    result["sessions"] = sessions
    result["totals"] = {
        "session_count": len(sessions),
        "present_seconds": round(sum(s["duration_s"] for s in sessions), 1),
        "first_appearance": sessions[0]["start"],
        "last_departure": sessions[-1]["end"],
    }
    return result


log = logging.getLogger("swahle.naturalist.sessionize")


def _downscale_jpeg(raw: bytes, max_px: int) -> bytes:
    img = Image.open(io.BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_px, max_px))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return out.getvalue()


def departure_frames(session_end: int, session_start: int, *, count: int,
                     pre_seconds: float, post_seconds: float,
                     max_px: int) -> list[bytes]:
    """Downscaled JPEG bytes for the frames bracketing a departure.

    Pulls hole-cam rows in [end - pre, end + post] (frames after the last
    high-prob frame show Cassini actually leaving), samples ``count`` of them
    evenly (always including the last), and reads+downscales them off disk.
    """
    start_ms = int(session_end - pre_seconds * 1000)
    stop_ms = int(session_end + post_seconds * 1000)
    session = SessionLocal()
    try:
        rows = (
            session.query(hole_camera.timestamp, hole_camera.path)
            .filter(hole_camera.timestamp >= start_ms)
            .filter(hole_camera.timestamp <= stop_ms)
            .order_by(hole_camera.timestamp)
            .all()
        )
    finally:
        session.close()

    paths = [r.path for r in rows if r.path]
    if not paths:
        return []
    if len(paths) > count:
        # evenly spaced indices including first and last
        step = (len(paths) - 1) / (count - 1) if count > 1 else 0
        idx = sorted({round(i * step) for i in range(count)})
        paths = [paths[i] for i in idx]

    frames: list[bytes] = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                frames.append(_downscale_jpeg(fh.read(), max_px))
        except (OSError, ValueError) as exc:
            log.warning("departure frame unreadable %s: %s", path, exc)
    return frames
