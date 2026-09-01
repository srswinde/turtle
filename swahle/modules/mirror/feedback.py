"""Mirror takeover feedback -- "that wasn't Cassini".

Records the call in ``mirror_feedback`` (audit trail for training), and for a
hole-cam sighting also flips ``hole_camera.hasTurtle`` to NO so the existing
labeled-frame retraining picks it up with no extra plumbing. Shed-cam frames
get the same treatment when a matching ``shed_camera`` row exists (yard-scan
frames written by the web app won't have one -- those are logged only).
"""
from __future__ import annotations

import logging

from shared.db import SessionLocal
from shared.models import HAS_TURTLE, hole_camera, shed_camera

from .models import record_feedback

log = logging.getLogger("swahle.mirror.feedback")

SIGHTING_TYPES = {"hole_sighting", "shed_sighting"}


def _flip_label(model, *, path: str | None, ts: int | None) -> int:
    """Set hasTurtle=NO on the matching frame row (by path, else by timestamp).
    Returns rows updated."""
    session = SessionLocal()
    try:
        n = 0
        if path:
            n = (session.query(model).filter(model.path == path)
                 .update({model.hasTurtle: HAS_TURTLE.NO}, synchronize_session=False))
        if not n and ts is not None:
            n = (session.query(model).filter(model.timestamp == ts)
                 .update({model.hasTurtle: HAS_TURTLE.NO}, synchronize_session=False))
        session.commit()
        return n
    except Exception:  # noqa: BLE001
        session.rollback()
        log.exception("false-positive label flip failed")
        return 0
    finally:
        session.close()


def record_false_positive(event: dict) -> dict:
    etype = str(event.get("type") or "")
    if etype not in SIGHTING_TYPES:
        return {"ok": False, "error": f"not a sighting event: {etype!r}"}

    image = event.get("image") or {}
    kind = image.get("kind")
    path = image.get("ref") if kind in ("hole", "shed") else None
    camera = kind if kind in ("hole", "shed") else ("hole" if "hole" in etype else "shed")
    ts = event.get("ts")

    model = hole_camera if camera == "hole" else shed_camera
    n = _flip_label(model, path=path, ts=ts)
    applied = (f"{model.__tablename__}.hasTurtle=NO ({n} row)" if n
               else f"logged (no {model.__tablename__} match)")

    record_feedback(ts=ts, camera=camera, path=path, event_type=etype,
                    verdict="false_positive", applied=applied)
    log.info("false positive recorded: %s %s -> %s", etype, path or ts, applied)
    return {"ok": True, "camera": camera, "applied": applied}
