"""turtle_db module: hole-cam gallery, classification/resnet tools, live feed."""
from __future__ import annotations

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from . import live, private, public


class TurtleDbModule(BaseModule):
    name = "turtle_db"

    def get_routers(self) -> list[APIRouter]:
        return [public.router, private.router, live.build_router()]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Gallery", "/gallery", "Hole-cam stills & Cassini's week.",
                    "\U0001F422", "public"),
            NavItem("Detections", "/detections", "Daily grouped sightings gallery.",
                    "\U0001F4C5", "public"),
            NavItem("Classify", "/app/classify", "Label hole-cam images (turtle / not).",
                    "\U0001F3F7️", "private"),
            NavItem("ResNet detections", "/app/resnet", "Object-detection boxes on hole-cam.",
                    "\U0001F50D", "private"),
            NavItem("ResNet training", "/app/resnet/train", "Label orientation for training.",
                    "\U0001F3AF", "private"),
        ]

    async def startup(self) -> None:
        await live.hub.start()

    async def shutdown(self) -> None:
        await live.hub.stop()
