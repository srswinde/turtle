"""dashboard module: Shelly/WLED control + hole/shed camera dashboard."""
from __future__ import annotations

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .pump_alert import pump_alert
from .router import hooks_router, router
from .services import hole_manager, registry, shed_manager


class DashboardModule(BaseModule):
    name = "dashboard"

    def get_routers(self) -> list[APIRouter]:
        return [router, hooks_router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Controls", "/app/controls",
                    "Lights, pump & WLED strips — all on one page.", "\U0001F39B️", "private"),
            NavItem("Webhooks", "/app/webhooks",
                    "Shelly device webhooks & the pump-run light alert.", "\U0001F517", "private"),
        ]

    async def startup(self) -> None:
        await hole_manager.start()
        await shed_manager.start()
        await pump_alert.start_poll()

    async def shutdown(self) -> None:
        await hole_manager.stop()
        await shed_manager.stop()
        await pump_alert.stop_poll()
        await registry.aclose()
