"""5-day forecast for the mirror, from api.weather.gov (NWS).

NWS keys its forecast off a gridpoint, not lat/lon directly, so we resolve
``/points/{lat},{lon}`` once and cache the forecast URL forever (a location's
gridpoint never changes); the forecast itself (12h day/night periods) is
re-fetched and cached for a while since NWS only updates it a few times a day.
No API key needed, but their API etiquette asks for an identifying
User-Agent -- see https://www.weather.gov/documentation/services-web-api.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import httpx

from app.settings import settings

log = logging.getLogger("swahle.mirror.forecast")

BASE = "https://api.weather.gov"
USER_AGENT = "swahle-mirror/1.0 (https://github.com/srswinde/turtle_db)"

_grid_cache: dict[tuple[float, float], str] = {}   # {(lat, lon): forecast_url}
_forecast_cache: dict = {"at": 0.0, "data": None}

# NWS's `shortForecast` is free text ("Chance Showers And Thunderstorms"), not
# a code -- match keywords to an emoji, same coarse approach as the open-meteo
# WMO table in ambient.py. Order matters: more specific first.
_KEYWORDS = [
    ("Thunderstorm", "⛈️"), ("Snow", "\U0001F328️"), ("Sleet", "\U0001F328️"),
    ("Freezing", "\U0001F327️"), ("Rain", "\U0001F327️"), ("Shower", "\U0001F326️"),
    ("Drizzle", "\U0001F326️"), ("Fog", "\U0001F32B️"), ("Haze", "\U0001F32B️"),
    ("Windy", "\U0001F4A8"), ("Overcast", "☁️"), ("Cloudy", "⛅"),
    ("Sunny", "☀️"), ("Clear", "☀️"),
]


def _icon_for(short: str) -> str:
    for kw, icon in _KEYWORDS:
        if kw.lower() in (short or "").lower():
            return icon
    return "•"


async def _forecast_url() -> str:
    key = (settings.mirror_weather_lat, settings.mirror_weather_lon)
    if key in _grid_cache:
        return _grid_cache[key]
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(f"{BASE}/points/{key[0]},{key[1]}")
    resp.raise_for_status()
    url = resp.json()["properties"]["forecast"]
    _grid_cache[key] = url
    return url


async def five_day() -> list[dict] | None:
    """Daily ``[{date, name, icon, short, high_f, low_f, precip_pct, wind}]``,
    soonest first. Cached 30 min; serves stale data rather than nothing on a
    transient failure (matches ``ambient.weather``'s behavior)."""
    now = time.time()
    if _forecast_cache["data"] and now - _forecast_cache["at"] < 1800:
        return _forecast_cache["data"]

    try:
        url = await _forecast_url()
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        periods = resp.json()["properties"]["periods"]
    except Exception as exc:  # noqa: BLE001 - serve stale/None on any failure
        log.warning("forecast fetch failed: %s", exc)
        return _forecast_cache["data"]

    days: list[dict] = []
    by_date: dict[str, dict] = {}
    for p in periods[:10]:  # ~5 days of day+night periods
        date = p["startTime"][:10]
        day = by_date.get(date)
        if day is None:
            day = {
                "date": date, "name": p["name"], "icon": "", "short": "",
                "high_f": None, "low_f": None, "precip_pct": None,
                "wind": p.get("windSpeed"),
            }
            by_date[date] = day
            days.append(day)
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        if p["isDaytime"]:
            day["high_f"] = p["temperature"]
        else:
            day["low_f"] = p["temperature"]
        # The daytime period sets the headline name/icon/text for the day;
        # if the first day only has a night period (forecast fetched in the
        # evening, e.g. just "Tonight"), fall back to it.
        if p["isDaytime"] or not day["short"]:
            day["name"] = p["name"]
            day["icon"] = _icon_for(p["shortForecast"])
            day["short"] = p["shortForecast"]
            if pop is not None:
                day["precip_pct"] = pop

    result = days[:5]
    _forecast_cache.update(at=now, data=result)
    return result
