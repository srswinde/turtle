"""Glanceable ambient data + quiet-hours state for the mirror's default screen.

Assembled into one payload (`/mirror/api/ambient`, polled ~60s):
- **cassini**  -- home now, or away since when (latest hi-prob hole-cam frame)
- **solar**    -- PV watts + battery SOC from the solar module's tables
- **weather**  -- current temp / today's hi-lo / condition + sunrise/sunset
                  (open-meteo, no key; cached 10 min)
- **quiet**    -- whether we're in quiet hours and how far to dim (the client
                  applies `filter: brightness()`), so the mirror goes dark at
                  night but still lights up for a takeover.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

import httpx

from app.settings import settings
from modules.naturalist.sessionize import MIN_PROB
from shared.db import SessionLocal
from shared.models import HAS_TURTLE, hole_camera

log = logging.getLogger("swahle.mirror.ambient")

_AZ = ZoneInfo("America/Phoenix")

# WMO weather codes -> (label, emoji). Coarse buckets are fine for a chip.
_WMO = {
    0: ("Clear", "☀️"), 1: ("Mostly clear", "\U0001F324️"),
    2: ("Partly cloudy", "⛅"), 3: ("Overcast", "☁️"),
    45: ("Fog", "\U0001F32B️"), 48: ("Fog", "\U0001F32B️"),
    51: ("Drizzle", "\U0001F326️"), 53: ("Drizzle", "\U0001F326️"),
    55: ("Drizzle", "\U0001F327️"), 56: ("Freezing drizzle", "\U0001F327️"),
    57: ("Freezing drizzle", "\U0001F327️"),
    61: ("Rain", "\U0001F327️"), 63: ("Rain", "\U0001F327️"),
    65: ("Heavy rain", "\U0001F327️"), 66: ("Freezing rain", "\U0001F327️"),
    67: ("Freezing rain", "\U0001F327️"),
    71: ("Snow", "\U0001F328️"), 73: ("Snow", "\U0001F328️"),
    75: ("Heavy snow", "❄️"), 77: ("Snow grains", "❄️"),
    80: ("Showers", "\U0001F326️"), 81: ("Showers", "\U0001F327️"),
    82: ("Violent showers", "⛈️"),
    85: ("Snow showers", "\U0001F328️"), 86: ("Snow showers", "\U0001F328️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm", "⛈️"),
    99: ("Thunderstorm", "⛈️"),
}

_weather_cache: dict = {"at": 0.0, "data": None}


# --- cassini -----------------------------------------------------------------

def cassini_status() -> dict:
    cutoff = int(time.time() * 1000) - settings.mirror_cassini_home_seconds * 1000
    session = SessionLocal()
    try:
        row = (
            session.query(hole_camera.timestamp)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .filter(hole_camera.prob > MIN_PROB)
            .order_by(hole_camera.timestamp.desc())
            .first()
        )
    finally:
        session.close()
    if not row:
        return {"home": False, "last_seen": None}
    return {"home": row.timestamp >= cutoff, "last_seen": row.timestamp}


# --- solar -----------------------------------------------------------------

def solar_status() -> dict:
    from modules.solar.models import latest_reading, latest_shunt_reading

    mppt = latest_reading("mppt") or {}
    shunt = latest_shunt_reading("shunt") or {}
    newest = max((mppt.get("ts") or 0), (shunt.get("ts") or 0))
    return {
        "pv_w": round(mppt["pv_power"]) if mppt.get("pv_power") is not None else None,
        "soc": round(shunt["soc"]) if shunt.get("soc") is not None else None,
        "batt_v": round(shunt.get("voltage") or mppt.get("battery_voltage") or 0, 1) or None,
        # readings arrive every few seconds; >10 min old => the BLE bridge is down
        "stale": bool(newest) and (time.time() * 1000 - newest) > 600_000,
    }


# --- weather -------------------------------------------------------------

async def weather() -> dict | None:
    now = time.time()
    if _weather_cache["data"] and now - _weather_cache["at"] < 600:
        return _weather_cache["data"]
    if not (settings.mirror_weather_lat or settings.mirror_weather_lon):
        return None
    params = {
        "latitude": settings.mirror_weather_lat,
        "longitude": settings.mirror_weather_lon,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset",
        "timezone": "America/Phoenix",
        "temperature_unit": "fahrenheit",
        "forecast_days": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        resp.raise_for_status()
        j = resp.json()
        cur, daily = j["current"], j["daily"]
        label, icon = _WMO.get(int(cur["weather_code"]), ("", "•"))
        data = {
            "temp_f": round(cur["temperature_2m"]),
            "high_f": round(daily["temperature_2m_max"][0]),
            "low_f": round(daily["temperature_2m_min"][0]),
            "icon": icon, "desc": label,
            "sunrise": daily["sunrise"][0], "sunset": daily["sunset"][0],
        }
    except Exception as exc:  # noqa: BLE001 - serve stale on any failure
        log.warning("weather fetch failed: %s", exc)
        return _weather_cache["data"]
    _weather_cache.update(at=now, data=data)
    return data


# --- quiet hours -------------------------------------------------------

_FALLBACK = {"sunrise": "06:30", "sunset": "19:30"}


def _resolve(spec: str, wx: dict | None, day: dt.date) -> dt.datetime:
    iso = (wx or {}).get(spec) if spec in ("sunrise", "sunset") else None
    if iso:
        return dt.datetime.fromisoformat(iso).replace(tzinfo=_AZ)
    hhmm = _FALLBACK.get(spec, spec)
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except ValueError:
        h, m = 22, 0
    return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=_AZ)


def quiet_status(wx: dict | None) -> dict:
    now = dt.datetime.now(_AZ)
    start = _resolve(settings.mirror_quiet_start, wx, now.date())
    end = _resolve(settings.mirror_quiet_end, wx, now.date())
    active = (start <= now < end) if start <= end else (now >= start or now < end)
    return {
        "active": active,
        "dim": settings.mirror_quiet_dim,
        "takeover_dim": settings.mirror_quiet_takeover_dim,
        "wake_seconds": settings.mirror_quiet_wake_seconds,
    }


# --- assembled payload -----------------------------------------------

async def ambient_payload() -> dict:
    from starlette.concurrency import run_in_threadpool

    wx = await weather()
    cassini = await run_in_threadpool(cassini_status)
    solar = await run_in_threadpool(solar_status)
    return {
        "cassini": cassini,
        "solar": solar,
        "weather": wx,
        "quiet": quiet_status(wx),
        "poll_seconds": settings.mirror_ambient_poll_seconds,
        "server_time": int(time.time() * 1000),
    }
