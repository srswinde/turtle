"""naturalist module: Cassini's-day timeline + departure analysis (Phases 0–3)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .acquisition import mirror_presence, session_verifier, shed_scanner, watcher
from .models import ensure_tables
from .router import router

log = logging.getLogger("swahle.naturalist")


class NaturalistModule(BaseModule):
    name = "naturalist"

    def get_routers(self) -> list[APIRouter]:
        return [router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Cassini's Day", "/app/naturalist",
                    "A timeline of when Cassini appeared at and left his hole.",
                    "\U0001F422", "private"),
            NavItem("OWLv2 Findings", "/app/naturalist/owlv2",
                    "Open-vocab tortoise detection: charts, lag times, and example catches.",
                    "\U0001F989", "private"),
        ]

    async def startup(self) -> None:
        try:
            ensure_tables()
        except Exception as exc:  # noqa: BLE001 - don't block app boot on DDL
            log.error("perception table ensure failed: %s", exc)
        await watcher.start_poll()
        await shed_scanner.start_poll()
        await session_verifier.start_poll()
        await mirror_presence.start_poll()

    async def shutdown(self) -> None:
        await watcher.stop_poll()
        await shed_scanner.stop_poll()
        await session_verifier.stop_poll()
        await mirror_presence.stop_poll()
