"""Aravaipa permit availability event log.

Own declarative metadata, unrelated to the turtle_db or naturalist schemas.
Records only CHANGES in quota (not every 2-minute poll), so the table stays a
readable "what happened when" log instead of a firehose of duplicate polls —
exactly what's needed to answer "when did it open" and "how fast did it sell".
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, Integer, String, func, inspect, text
from sqlalchemy.orm import declarative_base

from shared.db import SessionLocal, engine

AravaipaBase = declarative_base()


class permit_availability_event(AravaipaBase):
    __tablename__ = "permit_availability_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    observed_at = Column(BigInteger)        # ms — when the poll saw this value
    permit_id = Column(String(32))
    division_id = Column(String(32))
    division_name = Column(String(64))      # denormalized for easy display
    date = Column(String(16))               # "2026-10-24" (the permit date)
    total = Column(Integer)
    remaining = Column(Integer)
    prev_total = Column(Integer)            # NULL on the first-ever sighting of this date
    prev_remaining = Column(Integer)
    # recreation.gov's own API exposes these but its calendar UI doesn't surface
    # them distinctly — undocumented, but real: a reserved/special allocation
    # released separately from the main quota (best guess from the field name;
    # recreation.gov doesn't document it anywhere in the permit's own content).
    is_secret_quota = Column(Boolean, default=False)
    show_walkup = Column(Boolean, default=False)


def ensure_tables() -> None:
    AravaipaBase.metadata.create_all(engine, checkfirst=True)
    # create_all only creates missing TABLES, not missing COLUMNS on an existing
    # one — add any columns from later model changes by hand (additive, nullable,
    # safe on a live table).
    insp = inspect(engine)
    if "permit_availability_event" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("permit_availability_event")}
    with engine.begin() as conn:
        if "is_secret_quota" not in existing:
            conn.execute(text(
                "ALTER TABLE permit_availability_event "
                "ADD COLUMN is_secret_quota BOOLEAN DEFAULT FALSE"))
        if "show_walkup" not in existing:
            conn.execute(text(
                "ALTER TABLE permit_availability_event "
                "ADD COLUMN show_walkup BOOLEAN DEFAULT FALSE"))


def _row_to_dict(r: permit_availability_event) -> dict:
    return {
        "observed_at": r.observed_at,
        "division_id": r.division_id,
        "division_name": r.division_name,
        "date": r.date,
        "total": r.total,
        "remaining": r.remaining,
        "prev_total": r.prev_total,
        "prev_remaining": r.prev_remaining,
        "is_secret_quota": bool(r.is_secret_quota),
        "show_walkup": bool(r.show_walkup),
    }


def latest_state(permit_id: str) -> dict[tuple[str, str], permit_availability_event]:
    """Most-recent event per (division_id, date) — used by the poller to detect
    changes without re-scanning the whole history each cycle."""
    session = SessionLocal()
    try:
        E = permit_availability_event
        sub = (
            session.query(E.division_id, E.date, func.max(E.id).label("max_id"))
            .filter(E.permit_id == permit_id)
            .group_by(E.division_id, E.date)
            .subquery()
        )
        rows = session.query(E).join(sub, E.id == sub.c.max_id).all()
    finally:
        session.close()
    return {(r.division_id, r.date): r for r in rows}


def save_event(*, observed_at: int, permit_id: str, division_id: str, division_name: str,
              date: str, total: int, remaining: int, prev_total: int | None,
              prev_remaining: int | None, is_secret_quota: bool = False,
              show_walkup: bool = False) -> None:
    session = SessionLocal()
    try:
        row = permit_availability_event(
            observed_at=observed_at, permit_id=permit_id, division_id=division_id,
            division_name=division_name, date=date, total=total, remaining=remaining,
            prev_total=prev_total, prev_remaining=prev_remaining,
            is_secret_quota=is_secret_quota, show_walkup=show_walkup,
        )
        session.add(row)
        session.commit()
    finally:
        session.close()


def recent_events(permit_id: str, *, limit: int = 500,
                  months: list[str] | None = None) -> list[dict]:
    session = SessionLocal()
    try:
        rows = (
            session.query(permit_availability_event)
            .filter(permit_availability_event.permit_id == permit_id)
            .order_by(permit_availability_event.id.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()
    events = [_row_to_dict(r) for r in reversed(rows)]  # chronological order
    if months:
        events = [e for e in events if e["date"][:7] in months]
    return events


def current_status(permit_id: str, months: list[str] | None = None) -> list[dict]:
    state = latest_state(permit_id)
    out = [
        {"division_id": did, "division_name": r.division_name, "date": date,
         "total": r.total, "remaining": r.remaining,
         "is_secret_quota": bool(r.is_secret_quota), "show_walkup": bool(r.show_walkup)}
        for (did, date), r in state.items()
        if not months or date[:7] in months
    ]
    out.sort(key=lambda x: (x["date"], x["division_id"]))
    return out
