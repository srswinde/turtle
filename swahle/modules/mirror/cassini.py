"""Cassini's-day data for the mirror, as a two-column split.

Reuses the naturalist sessionizer + shed-detection/sighting/verification
queries wholesale -- the same enrichment ``/app/api/naturalist/day`` (see
modules/naturalist/router.py, the ``/app/naturalist`` page) attaches to each
session, just without the on-demand "Analyze exit" LLM trigger (a kiosk tap
shouldn't fire a paid Claude call) -- a session only shows exit/sighting info
here if it's already been analyzed from the main dashboard. Only the image
URLs are rewritten to this app's ``/mirror/img`` prefix.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from modules.naturalist.models import (
    get_departures_for_day,
    get_session_checks_for_day,
    get_shed_detections_for_day,
    get_sightings_for_day,
)
from modules.naturalist.sessionize import _day_bounds_ms, day_sessions

from .paths import rewrite

_ARIZONA = ZoneInfo("America/Phoenix")


def _today() -> dt.date:
    return dt.datetime.now(_ARIZONA).date()


def day_view(date_str: str | None) -> dict:
    day = dt.date.fromisoformat(date_str) if date_str else _today()

    sessions = day_sessions(day)
    start_ms, end_ms = _day_bounds_ms(day)
    departures = get_departures_for_day(start_ms, end_ms)
    sightings = get_sightings_for_day(start_ms, end_ms)
    checks = get_session_checks_for_day(start_ms, end_ms)

    for s in sessions.get("sessions", []):
        s["keyframes"] = {k: rewrite(v) for k, v in s.get("keyframes", {}).items()}
        s["exit"] = departures.get(s["end"])
        sighting = sightings.get(s["end"])
        if sighting and sighting.get("image_url"):
            sighting["image_url"] = rewrite(sighting["image_url"])
        s["sighting"] = sighting
        s["verification"] = checks.get(s["end"])

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
