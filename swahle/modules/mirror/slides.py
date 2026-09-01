"""Slideshow pool builder.

The mirror's ambient state is a shuffle of past high-probability shed-cam
tortoise detections. People must never appear on it, so every candidate frame
is vetted three ways before it can enter the pool:

1. ``shed_camera_metadata.is_human_detected`` -- the camera's own human flag.
2. a nearby ``shed_camera_resnet_detection`` row with ``class_name='person'``.
3. an OWLv2 re-check with a "person" prompt (``mirror_slide_person_threshold``).

Verdicts are cached in ``mirror_slide`` so this only pays the cost once per
frame. ``build_pool`` runs on a slow loop in the mirror app; ``current_pool``
is what the client fetches.
"""
from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool

from app.settings import settings
from shared.db import SessionLocal
from shared.models import (
    HAS_HUMAN,
    shed_camera_metadata,
    shed_camera_resnet_detection,
)

from .models import clean_slides, get_verdicts, save_verdict
from .paths import shed_path_to_url

log = logging.getLogger("swahle.mirror.slides")

_PERSON_PROMPTS = ["a person", "people", "a man", "a woman"]
_NEAR_MS = 3000  # a resnet 'person' row this close to the frame counts as the same scene


def _candidate_rows() -> list[dict]:
    """Top tortoise detections in the window, best score first. Overfetched so
    rejections still leave a full pool."""
    import datetime as dt

    from modules.naturalist.models import shed_detection

    cutoff = int(
        (dt.datetime.now() - dt.timedelta(days=settings.mirror_slide_window_days)).timestamp()
        * 1000
    )
    session = SessionLocal()
    try:
        rows = (
            session.query(shed_detection.ts, shed_detection.score, shed_detection.path)
            .filter(shed_detection.ts >= cutoff)
            .order_by(shed_detection.score.desc())
            .limit(settings.mirror_slide_pool_size * 3)
            .all()
        )
        return [{"ts": r.ts, "score": r.score, "path": r.path} for r in rows if r.path]
    finally:
        session.close()


def _has_person_in_db(ts: int) -> bool:
    session = SessionLocal()
    try:
        meta = (
            session.query(shed_camera_metadata.is_human_detected)
            .filter(shed_camera_metadata.timestamp == ts)
            .first()
        )
        if meta and meta.is_human_detected == HAS_HUMAN.YES:
            return True
        hit = (
            session.query(shed_camera_resnet_detection.timestamp)
            .filter(shed_camera_resnet_detection.timestamp >= ts - _NEAR_MS)
            .filter(shed_camera_resnet_detection.timestamp <= ts + _NEAR_MS)
            .filter(shed_camera_resnet_detection.class_name == "person")
            .first()
        )
        return hit is not None
    finally:
        session.close()


async def _owlv2_has_person(data: bytes) -> bool:
    from modules.naturalist.acquisition import _detect_tortoise  # reuse the ml_service call

    try:
        hit = await _detect_tortoise(data, prompts=_PERSON_PROMPTS)
    except Exception as exc:  # noqa: BLE001
        log.warning("OWLv2 person re-check failed: %s", exc)
        return True  # can't verify -> don't risk it
    return bool(hit and hit["score"] > settings.mirror_slide_person_threshold)


async def build_pool() -> int:
    """Vet any not-yet-settled candidates. Returns the count newly judged.

    ``unreadable`` verdicts are retried on every build (a frame the ingest
    wrote with the wrong perms becomes readable once that's fixed); every other
    reason is final."""
    candidates = await run_in_threadpool(_candidate_rows)
    known = await run_in_threadpool(get_verdicts, [c["path"] for c in candidates])
    judged = 0
    for c in candidates:
        if known.get(c["path"], "unreadable") != "unreadable":
            continue
        try:
            with open(c["path"], "rb") as fh:
                data = fh.read()
        except OSError:
            reason, ok = "unreadable", False
        else:
            if await run_in_threadpool(_has_person_in_db, c["ts"]):
                reason, ok = "meta", False
            elif await _owlv2_has_person(data):
                reason, ok = "owlv2", False
            else:
                reason, ok = "clean", True
        await run_in_threadpool(
            save_verdict, path=c["path"], ts=c["ts"], score=c["score"], ok=ok, reason=reason
        )
        judged += 1
    if judged:
        log.info("slide pool: judged %d frame(s)", judged)
    return judged


def current_pool() -> list[dict]:
    """Showable slides as ``{url, ts, score}``, highest score first."""
    return [
        {"url": shed_path_to_url(s["path"]), "ts": s["ts"], "score": s["score"]}
        for s in clean_slides(settings.mirror_slide_pool_size)
        if shed_path_to_url(s["path"])
    ]
