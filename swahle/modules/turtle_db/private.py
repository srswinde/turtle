"""Private zone: classification tool, resnet detections, and training labeling.

Mounted at ``/app`` behind ``require_user`` (defense-in-depth behind Cloudflare
Access). This is DB-internal / labeling surface, not for the public.
"""
from __future__ import annotations

import datetime
import logging

import pandas as pd
from dateutil.parser import ParserError, parse
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.deps import require_user
from app.templating import templates
from shared.db import get_session
from shared.models import (
    HAS_TURTLE,
    cassini_orientation,
    hole_camera,
    hole_camera_resnet_detection,
)

log = logging.getLogger("swahle.turtle_db.private")

HOLE_DISK_ROOT = "/mnt/nfs/hole-cam/"
HOLE_URL_PREFIX = "/hole-cam/static/"

router = APIRouter(prefix="/app", tags=["private"], dependencies=[Depends(require_user)])


def _rewrite(series: pd.Series) -> pd.Series:
    return series.str.replace(HOLE_DISK_ROOT, HOLE_URL_PREFIX, regex=False)


def _enum_to_value(df: pd.DataFrame, col: str = "hasTurtle") -> pd.DataFrame:
    if col in df.columns:
        df[col] = df[col].apply(lambda e: e.value if hasattr(e, "value") else e)
    return df


def _parse_day(raw: str | None) -> datetime.datetime:
    if not raw:
        return datetime.datetime.now()
    try:
        return parse(raw)
    except (ParserError, TypeError, ValueError):
        return datetime.datetime.now()


# --- Classification labeling tool ------------------------------------------

@router.get("/classify", response_class=HTMLResponse)
async def classify_page(request: Request):
    return templates.TemplateResponse(request, "hole.html")


@router.get("/api/hole-cam/uncategorized")
async def uncategorized(timestamp: int, session: Session = Depends(get_session)):
    """Hole-cam stills in the 30s window starting 30s before ``timestamp`` (ms)."""
    start = timestamp - 30 * 1000
    query = (
        session.query(hole_camera)
        .filter(hole_camera.timestamp > start)
        .filter(hole_camera.timestamp < start + 30 * 1000)
        .order_by(hole_camera.timestamp)
    )
    df = pd.read_sql(query.statement, query.session.bind)
    if len(df):
        df["path"] = _rewrite(df["path"])
        df = _enum_to_value(df)
    return JSONResponse(df.to_dict())


@router.post("/api/hole-cam/label")
async def label(request: Request, session: Session = Depends(get_session)):
    """Record a turtle/no-turtle label (and click position) for one still."""
    data = await request.json()

    if data["has_turtle"] is True:
        has_turtle = HAS_TURTLE.YES
    elif data["has_turtle"] is False:
        has_turtle = HAS_TURTLE.NO
    else:
        return JSONResponse({"detail": "invalid has_turtle"}, status_code=400)

    timestamp = data["timestamp"]
    dt = datetime.datetime.fromtimestamp(timestamp / 1000)
    if dt.year < 2000 or dt.year > 2040:
        return JSONResponse({"detail": f"invalid timestamp {dt}"}, status_code=400)

    if has_turtle == HAS_TURTLE.YES:
        x, y = float(data["x"]), float(data["y"])
        if not (0 <= x <= 1) or not (0 <= y <= 1):
            return JSONResponse({"detail": "x/y out of range"}, status_code=400)

    row = session.query(hole_camera).filter(hole_camera.timestamp == timestamp).first()
    if row is None:
        return JSONResponse({"detail": "no such row"}, status_code=404)
    row.hasTurtle = has_turtle
    session.commit()
    return JSONResponse("success")


# --- Resnet detections ------------------------------------------------------

@router.get("/resnet", response_class=HTMLResponse)
async def resnet_page(request: Request):
    return templates.TemplateResponse(request, "hole-resnet.html")


@router.post("/api/resnet")
async def resnet(request: Request, session: Session = Depends(get_session)):
    data: dict = {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        pass

    date = _parse_day(data.get("date"))
    midnight = datetime.datetime(date.year, date.month, date.day)
    next_midnight = midnight + datetime.timedelta(days=1)

    query = (
        session.query(hole_camera_resnet_detection)
        .filter(hole_camera_resnet_detection.timestamp > midnight.timestamp() * 1000)
        .filter(hole_camera_resnet_detection.timestamp < next_midnight.timestamp() * 1000)
    )
    df = pd.read_sql(query.statement, query.session.bind)
    if len(df):
        df["url"] = _rewrite(df["path"])
        del df["path"]
    return JSONResponse(df.to_dict())


# --- Resnet training labeling ----------------------------------------------

@router.get("/resnet/train", response_class=HTMLResponse)
async def resnet_train_page(request: Request):
    return templates.TemplateResponse(request, "hole_resnet_train.html")


@router.post("/api/resnet/train")
async def resnet_train(request: Request, session: Session = Depends(get_session)):
    """Resnet detections for a day that haven't yet been given an orientation."""
    data: dict = {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        pass

    date = _parse_day(data.get("date"))
    midnight = datetime.datetime(date.year, date.month, date.day)
    next_midnight = midnight + datetime.timedelta(days=1)

    labeled = session.query(cassini_orientation.timestamp).subquery()
    query = (
        session.query(hole_camera_resnet_detection)
        .filter(hole_camera_resnet_detection.timestamp > midnight.timestamp() * 1000)
        .filter(hole_camera_resnet_detection.timestamp < next_midnight.timestamp() * 1000)
        .filter(~hole_camera_resnet_detection.timestamp.in_(labeled))
    )
    df = pd.read_sql(query.statement, query.session.bind)
    if len(df):
        df["url"] = _rewrite(df["path"])
        del df["path"]
    df_dict = df.to_dict()
    df_dict["dates"] = [midnight.isoformat(), next_midnight.isoformat()]
    return JSONResponse(df_dict)


@router.post("/api/resnet/train/update")
async def resnet_train_update(request: Request, session: Session = Depends(get_session)):
    """Persist orientation labels produced by the training UI."""
    data = await request.json()

    new_rows = []
    for datum in data:
        row = cassini_orientation()
        for values in datum:
            row.timestamp = int(values["timestamp"])
            row.x1 = values["x1"]
            row.y1 = values["y1"]
            row.x2 = values["x2"]
            row.y2 = values["y2"]
            row.orientation = values["direction"]
        new_rows.append(row)

    session.add_all(new_rows)
    session.commit()
    return JSONResponse("success")
