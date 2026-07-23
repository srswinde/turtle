"""Public zone: hole-cam still gallery + read-only data endpoints.

Mounted at ``/`` with no auth. Serves ONLY hole-cam stills and the data that
drives the public landing page (latest image, temperatures, detection thumbs).
No movies and no shed-cam here — those live in the private zone.
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

import pandas as pd
from dateutil.parser import ParserError, parse
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.settings import settings
from app.templating import templates
from shared.db import get_session
from shared.models import HAS_TURTLE, hole_camera, temp_sensors
from shared.staticfiles import serve_file

log = logging.getLogger("swahle.turtle_db.public")

router = APIRouter(tags=["public"])

# Legacy on-disk root -> public URL prefix for hole-cam stills.
HOLE_DISK_ROOT = "/mnt/nfs/hole-cam/"
HOLE_URL_PREFIX = "/hole-cam/static/"

# Detections are bucketed by Arizona calendar day (no DST -- fixed UTC-7 year
# round), NOT the server's local time -- the swahle-web container itself runs
# in UTC, so a naive datetime().timestamp() call would compute "midnight" as
# UTC midnight (5pm the previous evening in Arizona), shifting the whole day
# window 7h early and mislabeling yesterday-evening sightings as "today".
_ARIZONA = ZoneInfo("America/Phoenix")


def _rewrite_hole_paths(series: pd.Series) -> pd.Series:
    return series.str.replace(HOLE_DISK_ROOT, HOLE_URL_PREFIX, regex=False)


def _parse_day(raw: str | None) -> datetime.datetime:
    """Parse a client-supplied date, defaulting to the current Arizona day."""
    if not raw:
        return datetime.datetime.now(_ARIZONA)
    try:
        return parse(raw)
    except (ParserError, TypeError, ValueError):
        log.warning("Invalid date %r; using now", raw)
        return datetime.datetime.now(_ARIZONA)


def _day_bounds(day: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """[start, end) of `day`'s Arizona calendar date, as Arizona-aware datetimes.

    `day` may be naive (e.g. a bare "Thu Jul 23 2026" from the client, which
    already names an Arizona calendar date with no time component) or
    tz-aware (e.g. an ISO timestamp) -- either way only its Y/M/D as seen in
    Arizona matters, since that's how detections are grouped.
    """
    if day.tzinfo is not None:
        day = day.astimezone(_ARIZONA)
    start = datetime.datetime(day.year, day.month, day.day, tzinfo=_ARIZONA)
    return start, start + datetime.timedelta(days=1)


@router.get("/gallery", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.post("/api/hole-cam/latest")
async def hole_cam_latest(request: Request, session: Session = Depends(get_session)):
    """Today's un-classified hole-cam stills (path + prob). Feeds the live image."""
    body = await request.body()
    data: dict = {}
    if body.strip():
        try:
            import json

            data = json.loads(body.decode("utf-8"))
        except ValueError:
            log.warning("Invalid JSON on /api/hole-cam/latest")

    day = _parse_day(data.get("timestamp"))
    midnight, next_midnight = _day_bounds(day)

    # hole_camera timestamps are milliseconds (unlike the seconds `images` table).
    query = (
        session.query(hole_camera)
        .filter(hole_camera.timestamp > midnight.timestamp() * 1000)
        .filter(hole_camera.timestamp < next_midnight.timestamp() * 1000)
    )
    df = pd.read_sql(query.statement, query.session.bind)
    if len(df) == 0:
        return JSONResponse({"path": {}, "prob": {}})

    df = df[df.hasTurtle == HAS_TURTLE.NULL].copy()
    df.index = df.timestamp
    df.path = _rewrite_hole_paths(df.path)
    df = df[["path", "prob"]]
    return JSONResponse(df.to_dict())


@router.post("/api/temperatures")
async def temperatures(request: Request, session: Session = Depends(get_session)):
    """Ambient/underground temperature series for the requested day."""
    data: dict = {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - empty/invalid body is fine
        pass

    day = _parse_day(data.get("timestamp"))
    midnight, next_midnight = _day_bounds(day)

    query = (
        session.query(temp_sensors)
        .filter(temp_sensors.timestamp > midnight.timestamp())
        .filter(temp_sensors.timestamp < next_midnight.timestamp())
    )
    df = pd.read_sql(query.statement, query.session.bind)
    if len(df) == 0:
        return JSONResponse({})

    df = df.groupby("address").apply(lambda x: x)
    addrs = df.index.get_level_values(0).unique()
    output: dict = {}
    for addr in addrs:
        if addr in settings.temp_map:
            output[settings.temp_map[addr]] = df.loc[addr].to_dict()
    return JSONResponse(output)


@router.get("/detections", response_class=HTMLResponse)
async def detections_page(request: Request):
    return templates.TemplateResponse(request, "images-playground.html")


@router.post("/api/detections")
async def detections(request: Request, session: Session = Depends(get_session)):
    """Grouped hole-cam detection intervals for a day (headline gallery)."""
    data: dict = {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        pass

    date = _parse_day(data.get("date"))
    minprob = float(data.get("minprob", 0.70))
    grouping_time = int(data.get("interval_seconds", 300))

    groups = _detect_interval(session, date, grouping_time, minprob)
    if len(groups) == 0:
        return JSONResponse({})

    group_dict: dict = {}
    for group in groups.index.get_level_values(0).unique():
        group_dict[int(group)] = dict(
            timestamp=[int(t) for t in groups.loc[group]["timestamp"].to_list()],
            prob=list(groups.loc[group]["prob"].values),
            url=list(groups.loc[group]["url"].values),
        )
    return JSONResponse(group_dict)


def _detect_interval(session, date, grouping_time=300, minprob=0.70):
    """Group hole-cam detections above ``minprob`` into time-separated bursts."""
    start, end = _day_bounds(date)

    query = (
        session.query(hole_camera)
        .filter(hole_camera.timestamp > start.timestamp() * 1000)
        .filter(hole_camera.timestamp < end.timestamp() * 1000)
    )
    df = pd.read_sql(query.statement, query.session.bind)
    if len(df) == 0:
        return df

    df["url"] = _rewrite_hole_paths(df["path"])
    df = df[df.prob > minprob].copy()
    if len(df) == 0:
        return df

    df.index = pd.to_datetime(df.timestamp, unit="ms")
    diff = df["prob"].index.to_series().diff().dt.total_seconds() > grouping_time
    grouped = df.groupby(diff.cumsum()).apply(lambda x: x)
    if len(grouped) == 0:
        return grouped
    grouped.index.set_names("group", level=0, inplace=True)
    return grouped


@router.get("/hole-cam/static/{path:path}")
async def hole_cam_static(path: str):
    """Serve hole-cam stills from the RAID mount (date-based directory layout)."""
    return serve_file(settings.hole_cam_dir, path)
