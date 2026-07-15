"""solar module: Victron SmartSolar MPPT Instant Readout ingest.

Shelly BLU gateway (BLE) -> /hooks/solar (this app decrypts) -> solar_reading table.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .models import ensure_tables
from .router import hooks_router, router

log = logging.getLogger("swahle.solar")


class SolarModule(BaseModule):
    name = "solar"

    def get_routers(self) -> list[APIRouter]:
        return [router, hooks_router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Solar", "/app/solar",
                    "SmartSolar MPPT telemetry — battery, PV input, charge state.",
                    "☀️", "private"),
        ]

    async def startup(self) -> None:
        try:
            ensure_tables()
        except Exception as exc:  # noqa: BLE001 - don't block app boot on DDL
            log.error("solar table ensure failed: %s", exc)
