"""pose module: DeepLabCut keypoint-labeling UI (Cassini facing-direction
pipeline). UI-only -- no background task.

See ``swahle/scripts/pose_export_dlc.py`` + ``pose_train.py`` for the
(manual, offline) DLC project export + training that consumes the labels
collected here, and ``modules/detect/pose.py`` for inference once a model
is trained.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from shared.module import BaseModule, NavItem

from .models import ensure_tables
from .router import router

log = logging.getLogger("swahle.pose")


class PoseModule(BaseModule):
    name = "pose"

    def get_routers(self) -> list[APIRouter]:
        return [router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Label Poses", "/app/pose/label",
                    "Click keypoints on hole-cam frames to build the tortoise "
                    "pose-estimation training set.",
                    "\U0001F3AF", "private"),
        ]

    async def startup(self) -> None:
        try:
            ensure_tables()
        except Exception as exc:  # noqa: BLE001 - don't block app boot on DDL
            log.error("pose_label table ensure failed: %s", exc)
