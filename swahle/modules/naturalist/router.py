"""Naturalist routes (private): Cassini's day timeline + departure analysis.

Phase 0 — presence sessions (appear/depart, time away, keyframes) from existing
hole-cam data. Phase 1 — Claude-vision analysis of each departure ("which way
did he go"), computed on demand and cached in ``cassini_departure``.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.deps import require_user
from app.settings import settings
from app.templating import templates
from shared.staticfiles import serve_file

from .acquisition import shed_scanner
from .models import get_departures_for_day, get_shed_detections_for_day, get_sightings_for_day, save_departure
from .sessionize import _day_bounds_ms, day_sessions, departure_frames
from .vision import VisionError, analyze_departure

router = APIRouter(prefix="/app", tags=["naturalist"], dependencies=[Depends(require_user)])

# swahle-web runs in UTC; "today" must mean the Arizona calendar day (see
# sessionize._ARIZONA / public.py), not the container's UTC date.
_ARIZONA = ZoneInfo("America/Phoenix")


@router.get("/naturalist", response_class=HTMLResponse)
async def naturalist_page(request: Request):
    return templates.TemplateResponse(request, "naturalist.html")


@router.get("/api/naturalist/day")
async def naturalist_day(date: str | None = Query(default=None)):
    try:
        day = dt.date.fromisoformat(date) if date else dt.datetime.now(_ARIZONA).date()
    except ValueError:
        return JSONResponse({"error": "date must be YYYY-MM-DD"}, status_code=400)
    data = await run_in_threadpool(day_sessions, day)
    # Attach any cached departure analysis + shed-cam sighting so the page shows
    # both without a re-call (sightings are populated by the Phase 2 watcher).
    start_ms, end_ms = _day_bounds_ms(day)
    cache = await run_in_threadpool(get_departures_for_day, start_ms, end_ms)
    sightings = await run_in_threadpool(get_sightings_for_day, start_ms, end_ms)
    for s in data["sessions"]:
        s["exit"] = cache.get(s["end"])
        s["sighting"] = sightings.get(s["end"])
    data["shed_detections"] = await run_in_threadpool(get_shed_detections_for_day, start_ms, end_ms)
    data["llm_ready"] = bool(settings.anthropic_api_key)
    data["acquisition_enabled"] = bool(settings.naturalist_auto_analyze)
    data["shed_scan_state"] = shed_scanner.state
    return JSONResponse(data)


def _toward_shed(direction: str) -> int | None:
    if not direction:
        return None
    unreachable = {d.strip() for d in settings.shed_unreachable_directions.split(",") if d.strip()}
    return 0 if direction in unreachable else 1


@router.post("/api/naturalist/analyze-departure")
async def analyze_departure_route(request: Request):
    body = await request.json()
    try:
        session_end = int(body["session_end"])
        session_start = int(body.get("session_start", session_end))
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "session_end required"}, status_code=400)

    frames = await run_in_threadpool(
        departure_frames, session_end, session_start,
        count=settings.departure_frames,
        pre_seconds=settings.departure_pre_seconds,
        post_seconds=settings.departure_post_seconds,
        max_px=settings.departure_max_px,
    )
    if not frames:
        return JSONResponse({"error": "no frames found around this departure"},
                            status_code=404)
    try:
        analysis = await analyze_departure(frames)
    except VisionError as exc:
        code = 503 if str(exc) == "no_api_key" else 502
        msg = ("Claude API key not configured (set SWAHLE_ANTHROPIC_API_KEY)."
               if str(exc) == "no_api_key" else f"Vision analysis failed: {exc}")
        return JSONResponse({"error": msg}, status_code=code)

    saved = await run_in_threadpool(
        save_departure,
        session_end=session_end, session_start=session_start, analysis=analysis,
        toward_shed=_toward_shed(analysis.get("direction", "")),
        model_name=settings.anthropic_model, frames_used=len(frames),
    )
    return JSONResponse(saved)


# --- OWLv2 investigation findings (offline analysis, scripts/owlv2_*.py) ----

@router.get("/naturalist/owlv2", response_class=HTMLResponse)
async def owlv2_findings_page(request: Request):
    return templates.TemplateResponse(request, "naturalist-owlv2.html")


@router.get("/api/naturalist/owlv2/findings")
async def owlv2_findings():
    path = settings.naturalist_data_dir / "owlv2" / "findings.json"

    def _read():
        return json.loads(path.read_text()) if path.is_file() else None

    data = await run_in_threadpool(_read)
    if data is None:
        return JSONResponse({"error": "findings not generated yet"}, status_code=404)
    return JSONResponse(data)


@router.get("/naturalist/owlv2/examples/{path:path}")
async def owlv2_example_image(path: str):
    return serve_file(settings.naturalist_data_dir / "owlv2" / "examples", path)
