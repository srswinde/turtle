"""Cassini's-day data for the mirror, as a two-column split.

Reuses the naturalist sessionizer and shed-detection query wholesale; only the
image URLs are rewritten to this app's ``/mirror/img`` prefix.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from modules.naturalist.models import get_shed_detections_for_day
from modules.naturalist.sessionize import _day_bounds_ms, day_sessions

from .paths import rewrite

_ARIZONA = ZoneInfo("America/Phoenix")


def _today() -> dt.date:
    return dt.datetime.now(_ARIZONA).date()


def day_view(date_str: str | None) -> dict:
    day = dt.date.fromisoformat(date_str) if date_str else _today()

    sessions = day_sessions(day)
    for s in sessions.get("sessions", []):
        s["keyframes"] = {k: rewrite(v) for k, v in s.get("keyframes", {}).items()}

    start_ms, end_ms = _day_bounds_ms(day)
    shed = get_shed_detections_for_day(start_ms, end_ms)
    for d in shed:
        d["image_url"] = rewrite(d.get("image_url"))

    return {
        "date": day.isoformat(),
        "is_today": day == _today(),
        "totals": sessions.get("totals", {}),
        "hole": sessions.get("sessions", []),
        "shed": shed,
    }
