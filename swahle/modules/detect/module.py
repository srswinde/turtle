"""detect module (web side): retrain UI + model version management.

Torch-free; the actual training runs as an isolated subprocess.
"""
from __future__ import annotations

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .router import router


class DetectModule(BaseModule):
    name = "detect"

    def get_routers(self) -> list[APIRouter]:
        return [router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Retrain", "/app/detect",
                    "Retrain the Cassini model & manage checkpoints.", "\U0001F9E0", "private"),
        ]
