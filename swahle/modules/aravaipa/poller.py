"""Background poller: every ``aravaipa_poll_seconds``, pulls the months listed
in ``aravaipa_months`` and records any CHANGE from the last known value as an
event — so the event log directly answers "when did this date open" and "how
fast did it get booked".

Runs as an asyncio task inside the web app (mirrors dashboard.pump_alert and
naturalist.acquisition.DepartureWatcher), not a separate process.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.settings import settings

from . import client
from .models import latest_state, save_event

log = logging.getLogger("swahle.aravaipa.poller")

_FALLBACK_DIVISION_NAMES = {"772": "East Entrance", "773": "West Entrance"}


class AravaipaPoller:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._division_names: dict[str, str] = dict(_FALLBACK_DIVISION_NAMES)

    async def start_poll(self) -> None:
        if not settings.aravaipa_password:
            log.info("aravaipa_password not set; poller disabled")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="aravaipa-poller")

    async def stop_poll(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            content = await client.fetch_permit_content(settings.aravaipa_permit_id)
            names = {k: v.get("name", k) for k, v in (content.get("divisions") or {}).items()}
            if names:
                self._division_names = names
        except Exception as exc:  # noqa: BLE001 - fall back to hardcoded names
            log.warning("could not fetch division names, using fallback: %s", exc)

        while True:
            try:
                await self._poll_once()
            except Exception:  # noqa: BLE001 - keep the poller alive across errors
                log.exception("aravaipa poll tick failed")
            await asyncio.sleep(settings.aravaipa_poll_seconds)

    async def _poll_once(self) -> None:
        permit_id = settings.aravaipa_permit_id
        state = latest_state(permit_id)
        now_ms = int(dt.datetime.now().timestamp() * 1000)
        changes = 0

        for ym_str in settings.aravaipa_months:
            year, month = (int(x) for x in ym_str.split("-"))
            try:
                data = await client.fetch_month(permit_id, year, month)
            except Exception as exc:  # noqa: BLE001 - one bad month shouldn't kill the cycle
                log.warning("fetch_month failed for %s: %s", ym_str, exc)
                continue

            for division_id, div_data in (data.get("availability") or {}).items():
                division_name = self._division_names.get(division_id, division_id)
                for date_str, info in (div_data.get("date_availability") or {}).items():
                    date = date_str[:10]
                    total = info.get("total", 0)
                    remaining = info.get("remaining", 0)
                    is_secret_quota = bool(info.get("is_secret_quota", False))
                    show_walkup = bool(info.get("show_walkup", False))
                    prev = state.get((division_id, date))
                    if (prev is not None and prev.total == total and prev.remaining == remaining
                            and bool(prev.is_secret_quota) == is_secret_quota
                            and bool(prev.show_walkup) == show_walkup):
                        continue
                    save_event(
                        observed_at=now_ms, permit_id=permit_id, division_id=division_id,
                        division_name=division_name, date=date, total=total, remaining=remaining,
                        prev_total=prev.total if prev else None,
                        prev_remaining=prev.remaining if prev else None,
                        is_secret_quota=is_secret_quota, show_walkup=show_walkup,
                    )
                    changes += 1

        if changes:
            log.info("aravaipa poll: %d change(s) recorded", changes)


poller = AravaipaPoller()
