"""Perception tables — the *high-level story* of Cassini's day.

These are new tables the naturalist module owns, kept on their own declarative
metadata so we never touch the legacy ``turtle_db.db`` schema shared with the
Tornado app. ``ensure_tables()`` creates them idempotently (CREATE TABLE IF NOT
EXISTS semantics) at module startup.

Phase 1 adds ``cassini_departure``: the cached result of the Claude-vision
analysis of how Cassini left the hole-cam frame, keyed by the session's end
timestamp so each departure is analyzed at most once.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Column, Float, Integer, String
from sqlalchemy.orm import declarative_base

from app.settings import settings
from shared.db import SessionLocal, engine

PerceptionBase = declarative_base()


class cassini_departure(PerceptionBase):
    __tablename__ = "cassini_departure"
    session_end = Column(BigInteger, primary_key=True)   # last high-prob frame ms
    session_start = Column(BigInteger)
    direction = Column(String(32))          # left/right/up/down/toward-camera/...
    confidence = Column(Float)
    present = Column(Integer, default=1)     # was a tortoise actually visible
    description = Column(String(512))
    toward_shed = Column(Integer)            # 1/0 vs shed_unreachable_directions; NULL=unknown
    model_name = Column(String(64))
    frames_used = Column(Integer)
    created = Column(BigInteger)




class shed_sighting(PerceptionBase):
    """Historical record of the old reactive-only v1 acquisition scans (kept
    read-only for past dates; nothing writes new rows here -- see
    ``shed_detection`` for the continuous scanner that replaced it).
    Keyed by the departure's session_end.
    """
    __tablename__ = "shed_sighting"
    departure_end = Column(BigInteger, primary_key=True)
    triggered_at = Column(BigInteger)        # when the scan started
    completed_at = Column(BigInteger)        # when it stopped (found or timed out)
    found = Column(Integer, default=0)       # 1/0
    score = Column(Float)
    path = Column(String(512))
    box_x1 = Column(Float)
    box_y1 = Column(Float)
    box_x2 = Column(Float)
    box_y2 = Column(Float)
    pan = Column(Float)                      # best-effort ptz_status() read; NULL if unavailable
    tilt = Column(Float)
    lag_seconds = Column(Float)              # departure -> found frame (NULL if not found)
    frames_checked = Column(Integer)


class cassini_session_check(PerceptionBase):
    """Phase 3: OWLv2 second-opinion verification of a hole-cam presence
    session -- catches whole-frame VGG classifier false positives (shadow
    flicker on the empty hole interior, lens debris) that a localized,
    differently-trained detector isn't fooled by. Computed once per session
    (keyed by session_end) by ``SessionVerifier``, cached here so
    day_sessions/recent_sessions don't need a live OWLv2 call on every page
    view. Phase 1 (2026-08-23): flag-only -- ``verified`` is surfaced in the
    UI but does not filter anything out of the timeline yet (no calibration
    data for OWLv2 on this close-up hole-cam domain). See naturalist memory.
    """
    __tablename__ = "cassini_session_check"
    session_end = Column(BigInteger, primary_key=True)
    session_start = Column(BigInteger)
    verified = Column(Integer)          # 1 confirmed / 0 rejected / NULL = ml_service unavailable
    score = Column(Float)               # OWLv2 score at the checked frame (NULL if no hit at all)
    checked_path = Column(String(512))  # which frame was sent (peak-prob frame)
    box_x1 = Column(Float)
    box_y1 = Column(Float)
    box_x2 = Column(Float)
    box_y2 = Column(Float)
    created = Column(BigInteger)


class shed_detection(PerceptionBase):
    """Phase 2 (continuous v2): a confirmed OWLv2 hit from the always-on
    daylight-hours shed-cam scan. Not tied to a hole-cam departure -- see
    naturalist memory (2026-07-16) for why the departure-triggered v1 was
    replaced: it silently missed real sightings whenever the departure's
    exit-direction classification was wrong.
    """
    __tablename__ = "shed_detection"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(BigInteger)                  # shed_camera frame timestamp (ms)
    score = Column(Float)
    path = Column(String(512))
    box_x1 = Column(Float)
    box_y1 = Column(Float)
    box_x2 = Column(Float)
    box_y2 = Column(Float)
    pan = Column(Float)
    tilt = Column(Float)
    created = Column(BigInteger)


def ensure_tables() -> None:
    PerceptionBase.metadata.create_all(engine, checkfirst=True)


def _row_to_dict(row: cassini_departure) -> dict:
    return {
        "session_end": row.session_end,
        "direction": row.direction,
        "confidence": row.confidence,
        "present": bool(row.present) if row.present is not None else None,
        "description": row.description,
        "toward_shed": (bool(row.toward_shed) if row.toward_shed is not None else None),
        "model": row.model_name,
        "frames_used": row.frames_used,
    }


def get_departure(session_end: int) -> dict | None:
    session = SessionLocal()
    try:
        row = session.get(cassini_departure, int(session_end))
        return _row_to_dict(row) if row else None
    finally:
        session.close()


def get_departures_for_day(start_ms: int, end_ms: int) -> dict[int, dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(cassini_departure)
            .filter(cassini_departure.session_end >= start_ms)
            .filter(cassini_departure.session_end < end_ms)
            .all()
        )
        return {r.session_end: _row_to_dict(r) for r in rows}
    finally:
        session.close()


def save_departure(*, session_end: int, session_start: int, analysis: dict,
                   toward_shed: int | None, model_name: str, frames_used: int) -> dict:
    session = SessionLocal()
    try:
        row = session.get(cassini_departure, int(session_end))
        if row is None:
            row = cassini_departure(session_end=int(session_end))
            session.add(row)
        row.session_start = int(session_start)
        row.direction = analysis.get("direction")
        row.confidence = float(analysis.get("confidence", 0.0) or 0.0)
        row.present = 1 if analysis.get("present", True) else 0
        row.description = (analysis.get("description") or "")[:512]
        row.toward_shed = toward_shed
        row.model_name = model_name
        row.frames_used = frames_used
        row.created = int(dt.datetime.now().timestamp() * 1000)
        session.commit()
        return get_departure(session_end)  # re-read as plain dict
    finally:
        session.close()


def _shed_path_to_url(path: str | None) -> str | None:
    if not path:
        return None
    root = str(settings.shed_cam_dir).rstrip("/") + "/"
    if not path.startswith(root):
        return None
    return "/app/shed-cam/images/" + path[len(root):]


def _sighting_to_dict(row: shed_sighting) -> dict:
    box = None
    if row.box_x1 is not None:
        box = [row.box_x1, row.box_y1, row.box_x2, row.box_y2]
    return {
        "departure_end": row.departure_end,
        "triggered_at": row.triggered_at,
        "completed_at": row.completed_at,
        "found": bool(row.found),
        "score": row.score,
        "path": row.path,
        "image_url": _shed_path_to_url(row.path),
        "box": box,
        "pan": row.pan,
        "tilt": row.tilt,
        "lag_seconds": row.lag_seconds,
        "frames_checked": row.frames_checked,
    }


def get_sighting(departure_end: int) -> dict | None:
    session = SessionLocal()
    try:
        row = session.get(shed_sighting, int(departure_end))
        return _sighting_to_dict(row) if row else None
    finally:
        session.close()


def get_sightings_for_day(start_ms: int, end_ms: int) -> dict[int, dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(shed_sighting)
            .filter(shed_sighting.departure_end >= start_ms)
            .filter(shed_sighting.departure_end < end_ms)
            .all()
        )
        return {r.departure_end: _sighting_to_dict(r) for r in rows}
    finally:
        session.close()


def _detection_to_dict(row: shed_detection) -> dict:
    box = None
    if row.box_x1 is not None:
        box = [row.box_x1, row.box_y1, row.box_x2, row.box_y2]
    return {
        "id": row.id,
        "ts": row.ts,
        "score": row.score,
        "path": row.path,
        "image_url": _shed_path_to_url(row.path),
        "box": box,
        "pan": row.pan,
        "tilt": row.tilt,
    }


def get_shed_detections_for_day(start_ms: int, end_ms: int) -> list[dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(shed_detection)
            .filter(shed_detection.ts >= start_ms)
            .filter(shed_detection.ts < end_ms)
            .order_by(shed_detection.ts)
            .all()
        )
        return [_detection_to_dict(r) for r in rows]
    finally:
        session.close()


def get_last_shed_detection_created() -> int | None:
    """Wall-clock time of the most recent logged hit, for cooldown gating."""
    session = SessionLocal()
    try:
        row = (
            session.query(shed_detection.created)
            .order_by(shed_detection.created.desc())
            .first()
        )
        return row.created if row else None
    finally:
        session.close()


def save_shed_detection(*, ts: int, score: float, path: str,
                        box: list[float] | None, pan: float | None,
                        tilt: float | None) -> dict:
    session = SessionLocal()
    try:
        row = shed_detection(ts=int(ts), score=score, path=path,
                             pan=pan, tilt=tilt,
                             created=int(dt.datetime.now().timestamp() * 1000))
        if box:
            row.box_x1, row.box_y1, row.box_x2, row.box_y2 = box
        session.add(row)
        session.commit()
        return _detection_to_dict(row)
    finally:
        session.close()


def _check_to_dict(row: cassini_session_check) -> dict:
    box = None
    if row.box_x1 is not None:
        box = [row.box_x1, row.box_y1, row.box_x2, row.box_y2]
    return {
        "session_end": row.session_end,
        "verified": (bool(row.verified) if row.verified is not None else None),
        "score": row.score,
        "checked_path": row.checked_path,
        "box": box,
    }


def get_session_check(session_end: int) -> dict | None:
    session = SessionLocal()
    try:
        row = session.get(cassini_session_check, int(session_end))
        return _check_to_dict(row) if row else None
    finally:
        session.close()


def get_session_checks_for_day(start_ms: int, end_ms: int) -> dict[int, dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(cassini_session_check)
            .filter(cassini_session_check.session_end >= start_ms)
            .filter(cassini_session_check.session_end < end_ms)
            .all()
        )
        return {r.session_end: _check_to_dict(r) for r in rows}
    finally:
        session.close()


def save_session_check(*, session_end: int, session_start: int, verified: int | None,
                       score: float | None, checked_path: str | None,
                       box: list[float] | None) -> dict:
    session = SessionLocal()
    try:
        row = session.get(cassini_session_check, int(session_end))
        if row is None:
            row = cassini_session_check(session_end=int(session_end))
            session.add(row)
        row.session_start = int(session_start)
        row.verified = verified
        row.score = score
        row.checked_path = checked_path
        if box:
            row.box_x1, row.box_y1, row.box_x2, row.box_y2 = box
        row.created = int(dt.datetime.now().timestamp() * 1000)
        session.commit()
        return get_session_check(session_end)
    finally:
        session.close()
