"""Polling caches for hole-cam / shed-cam detections (ported from dashboard).

Each manager runs a background poll loop, reading the DB off the event loop in a
thread and caching the latest DataFrame. Ported to use ``shared.db`` /
``shared.models`` instead of the legacy ``turtle_db.db`` helpers.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import pandas as pd

from shared.db import SessionLocal
from shared.models import (
    HAS_TURTLE,
    hole_camera,
    shed_camera_resnet_detection,
)

log = logging.getLogger("swahle.dashboard.data")

# Hole-cam stills are served publicly at /hole-cam/static/ ; the dashboard hero
# image URL is normalized against the site origin on the client.
HOLE_DISK_ROOT = "/mnt/nfs/hole-cam/"
HOLE_URL_PREFIX = "/hole-cam/static/"


def _detect_interval(grouping_time: int, minprob: float, history_days: int) -> pd.DataFrame:
    date = datetime.datetime.now()
    end = datetime.datetime(date.year, date.month, date.day, 23, 59, 59)
    start = end - datetime.timedelta(days=history_days)

    session = SessionLocal()
    try:
        query = (
            session.query(hole_camera)
            .filter(hole_camera.timestamp > start.timestamp() * 1000)
            .filter(hole_camera.timestamp < end.timestamp() * 1000)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
        )
        df = pd.read_sql(query.statement, session.bind)
    finally:
        session.close()

    if len(df) == 0:
        return df
    df["url"] = df["path"].str.replace(HOLE_DISK_ROOT, HOLE_URL_PREFIX, regex=False)
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


class HoleCamDataManager:
    """Refresh and cache hole-cam detections; expose the most recent one."""

    def __init__(self, *, interval_seconds: float, grouping_seconds: int = 300,
                 min_probability: float = 0.70, history_days: int = 7) -> None:
        self._interval = max(interval_seconds, 0.1)
        self._grouping_seconds = grouping_seconds
        self._min_probability = min_probability
        self._history_days = history_days
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._task: Optional[asyncio.Task] = None
        self._latest: Optional[pd.DataFrame] = None
        self._recent_detection: Optional[dict[str, Any]] = None
        self._lock = asyncio.Lock()

    async def _query(self) -> pd.DataFrame:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, _detect_interval,
            self._grouping_seconds, self._min_probability, self._history_days,
        )

    async def refresh(self) -> pd.DataFrame:
        detections = await self._query()
        recent: Optional[dict[str, Any]] = None
        if detections is not None and not detections.empty:
            try:
                group_idx = detections.index.get_level_values("group")[-1]
                recent_group = detections.xs(group_idx, level="group")
                if not recent_group.empty:
                    best = recent_group.loc[recent_group["prob"].idxmax()]
                    ts = best.get("timestamp")
                    try:
                        ts_int = int(ts) if ts is not None else None
                    except (TypeError, ValueError):
                        ts_int = None
                    recent = {
                        "path": best.get("path"),
                        "url": best.get("url"),
                        "timestamp": ts_int,
                        "probability": float(best.get("prob", 0.0) or 0.0),
                    }
            except (KeyError, IndexError, ValueError) as exc:
                log.debug("Unable to determine recent hole-cam detection: %s", exc)

        async with self._lock:
            self._latest = detections
            self._recent_detection = recent
        return detections

    async def latest(self) -> Optional[pd.DataFrame]:
        async with self._lock:
            return self._latest

    def recent_detection(self) -> Optional[dict[str, Any]]:
        return dict(self._recent_detection) if self._recent_detection else None

    async def is_cassini(self, timestamp: int, has_cassini: bool) -> None:
        session = SessionLocal()
        try:
            record = (
                session.query(hole_camera)
                .filter(hole_camera.timestamp == int(timestamp))
                .one_or_none()
            )
            if record is None:
                raise ValueError(f"No hole camera record for timestamp {timestamp}")
            record.hasTurtle = HAS_TURTLE.YES if has_cassini else HAS_TURTLE.NO
            session.commit()
        finally:
            session.close()

    async def _poll(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("hole-cam poll failed: %s", exc)
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll(), name="hole-cam-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._executor.shutdown(wait=False)


class ShedCamDataManager:
    """Refresh and cache recent shed-cam resnet detections."""

    def __init__(self, *, interval_seconds: float, default_hours: int) -> None:
        self._interval = max(interval_seconds, 0.1)
        self._default_hours = default_hours
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._task: Optional[asyncio.Task] = None
        self._latest: Optional[pd.DataFrame] = None
        self._lock = asyncio.Lock()

    def _query_sync(self, nhours: int) -> pd.DataFrame:
        session = SessionLocal()
        try:
            start = datetime.datetime.now() - datetime.timedelta(hours=nhours)
            query = (
                session.query(shed_camera_resnet_detection)
                .order_by(shed_camera_resnet_detection.timestamp.desc())
                .filter(shed_camera_resnet_detection.timestamp >= start.timestamp() * 1000)
            )
            return pd.read_sql(query.statement, session.bind)
        finally:
            session.close()

    async def refresh(self, nhours: Optional[int] = None) -> pd.DataFrame:
        loop = asyncio.get_running_loop()
        hours = nhours if nhours is not None else self._default_hours
        df = await loop.run_in_executor(self._executor, self._query_sync, hours)
        async with self._lock:
            self._latest = df
        return df

    async def latest(self) -> Optional[pd.DataFrame]:
        async with self._lock:
            return self._latest

    async def _poll(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("shed-cam poll failed: %s", exc)
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll(), name="shed-cam-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._executor.shutdown(wait=False)
