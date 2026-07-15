"""solar module: Victron SmartSolar MPPT Instant Readout ingest.

Shelly BLU gateway (BLE) -> /hooks/solar (this app decrypts) -> SolarStore.
"""
from __future__ import annotations

from fastapi import APIRouter

from shared.module import BaseModule

from .router import hooks_router, router


class SolarModule(BaseModule):
    name = "solar"

    def get_routers(self) -> list[APIRouter]:
        return [router, hooks_router]
