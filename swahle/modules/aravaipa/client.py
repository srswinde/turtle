"""Thin client for recreation.gov's permit availability API.

This is the same JSON API their own React app calls (see the network requests
made by https://www.recreation.gov/permits/{id}/registration/detailed-availability)
— no API key or auth needed, just a normal GET with a browser-like User-Agent.
"""
from __future__ import annotations

import httpx

BASE = "https://www.recreation.gov/api"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


async def fetch_permit_content(permit_id: str) -> dict:
    """Permit metadata, including the {division_id: {name, ...}} map."""
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(f"{BASE}/permitcontent/{permit_id}")
    resp.raise_for_status()
    return resp.json()["payload"]


async def fetch_month(permit_id: str, year: int, month: int) -> dict:
    """One month's availability grid: {division_id: {date_availability: {date: {total, remaining}}}}."""
    start_date = f"{year:04d}-{month:02d}-01T00:00:00.000Z"
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(
            f"{BASE}/permits/{permit_id}/availability/month",
            params={"start_date": start_date},
        )
    resp.raise_for_status()
    return resp.json()["payload"]
