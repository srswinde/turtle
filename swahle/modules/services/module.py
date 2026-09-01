"""services module (web side): start/stop/monitor the standalone scripts."""
from __future__ import annotations

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .router import router


class ServicesModule(BaseModule):
    name = "services"

    def get_routers(self) -> list[APIRouter]:
        return [router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Services", "/app/services",
                    "Start/stop the camera & analysis scripts.", "\U0001F6E0️", "private"),
        ]
