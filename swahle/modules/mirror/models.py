"""``mirror_slide`` — cache of person-vetted slideshow candidates.

Own declarative base + ``create_all`` (this repo has no Alembic), same pattern
as ``modules/naturalist/models.py``. One row per shed-cam frame we've already
judged: ``ok`` true means no person was found and it's safe to show on the
mirror. Building the pool is expensive (a DB join plus an OWLv2 call per new
frame), so once judged a frame is never re-checked.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, String
from sqlalchemy.orm import declarative_base

from shared.db import SessionLocal, engine

MirrorBase = declarative_base()


class mirror_slide(MirrorBase):
    __tablename__ = "mirror_slide"
    path = Column(String(512), primary_key=True)   # absolute shed-cam frame path
    ts = Column(BigInteger)                         # shed_camera frame ts (ms)
    score = Column(Float)                           # tortoise detection score
    ok = Column(Boolean)                            # True = no person -> showable
    reason = Column(String(32))                     # "clean" | "meta" | "resnet" | "owlv2"
    checked = Column(BigInteger)                    # wall-clock ms of the vet


class mirror_feedback(MirrorBase):
    """Human calls made from the mirror's takeover screen -- currently just
    "that detection was wrong". An audit log the retraining pipeline can pull
    from; for hole-cam we also flip ``hole_camera.hasTurtle`` so it feeds the
    existing labeled-frame trainer directly."""
    __tablename__ = "mirror_feedback"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(BigInteger)            # the detection frame's timestamp (ms), if known
    camera = Column(String(16))        # "hole" | "shed" | ""
    path = Column(String(512))         # frame path, if known
    event_type = Column(String(32))    # "hole_sighting" | "shed_sighting"
    verdict = Column(String(16))       # "false_positive"
    applied = Column(String(96))       # what we did downstream
    created = Column(BigInteger)


def record_feedback(*, ts: int | None, camera: str, path: str | None,
                    event_type: str, verdict: str, applied: str) -> None:
    session = SessionLocal()
    try:
        session.add(mirror_feedback(
            ts=ts, camera=camera or "", path=path, event_type=event_type,
            verdict=verdict, applied=applied,
            created=int(dt.datetime.now().timestamp() * 1000),
        ))
        session.commit()
    finally:
        session.close()


def ensure_tables() -> None:
    MirrorBase.metadata.create_all(engine, checkfirst=True)


def get_verdicts(paths: list[str]) -> dict[str, str]:
    """``{path: reason}`` for paths already judged. ``reason`` is
    ``clean``/``meta``/``owlv2`` (settled) or ``unreadable`` (retry next build)."""
    if not paths:
        return {}
    session = SessionLocal()
    try:
        rows = (
            session.query(mirror_slide.path, mirror_slide.reason)
            .filter(mirror_slide.path.in_(paths))
            .all()
        )
        return {r.path: (r.reason or "") for r in rows}
    finally:
        session.close()


def save_verdict(*, path: str, ts: int, score: float, ok: bool, reason: str) -> None:
    session = SessionLocal()
    try:
        row = session.get(mirror_slide, path)
        if row is None:
            row = mirror_slide(path=path)
            session.add(row)
        row.ts, row.score, row.ok, row.reason = ts, score, ok, reason
        row.checked = int(dt.datetime.now().timestamp() * 1000)
        session.commit()
    finally:
        session.close()


def clean_slides(limit: int) -> list[dict]:
    """Showable frames, highest tortoise-score first."""
    session = SessionLocal()
    try:
        rows = (
            session.query(mirror_slide.path, mirror_slide.ts, mirror_slide.score)
            .filter(mirror_slide.ok.is_(True))
            .order_by(mirror_slide.score.desc())
            .limit(limit)
            .all()
        )
        return [{"path": r.path, "ts": r.ts, "score": r.score} for r in rows]
    finally:
        session.close()
