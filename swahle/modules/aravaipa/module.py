"""aravaipa module: public, password-gated Aravaipa Canyon permit-availability watcher."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .models import ensure_tables
from .poller import poller
from .router import router

log = logging.getLogger("swahle.aravaipa")


class AravaipaModule(BaseModule):
    name = "aravaipa"

    def get_routers(self) -> list[APIRouter]:
        return [router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Aravaipa Permits", "/aravaipa",
                    "Live Aravaipa Canyon permit availability — password required.",
                    "\U0001F3D5️", "public"),
        ]

    async def startup(self) -> None:
        try:
            ensure_tables()
        except Exception as exc:  # noqa: BLE001 - don't block app boot on DDL
            log.error("aravaipa table ensure failed: %s", exc)
        await poller.start_poll()

    async def shutdown(self) -> None:
        await poller.stop_poll()
