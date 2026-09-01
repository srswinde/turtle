"""ORM models.

Re-exported from the legacy ``turtle_db.db`` module (imported at repo root) so
both apps share one source of truth for the schema during the migration. Import
models from here — never from ``turtle_db.db`` directly — so the eventual move
into the module is a one-file change.
"""
from __future__ import annotations

from turtle_db.db import (  # noqa: F401  (re-export)
    HAS_HUMAN,
    HAS_MOTION,
    HAS_TURTLE,
    cassini_orientation,
    conditions,
    hole_camera,
    hole_camera_metadata,
    hole_camera_resnet_detection,
    images,
    notifications,
    shed_camera,
    shed_camera_metadata,
    shed_camera_resnet_detection,
    temp_sensors,
)

__all__ = [
    "HAS_HUMAN",
    "HAS_MOTION",
    "HAS_TURTLE",
    "cassini_orientation",
    "conditions",
    "hole_camera",
    "hole_camera_metadata",
    "hole_camera_resnet_detection",
    "images",
    "notifications",
    "shed_camera",
    "shed_camera_metadata",
    "shed_camera_resnet_detection",
    "temp_sensors",
]
