"""Module-level singletons shared between the router and the module lifecycle.

Constructed at import (cheap — no I/O); pollers are started in the module's
``startup`` and stopped in ``shutdown``.
"""
from __future__ import annotations

from app.settings import settings
from shared.devices.registry import DeviceRegistry

from .data import HoleCamDataManager, ShedCamDataManager

hole_manager = HoleCamDataManager(interval_seconds=settings.hole_poll_seconds)
shed_manager = ShedCamDataManager(
    interval_seconds=settings.shed_poll_seconds,
    default_hours=settings.shed_history_hours,
)
registry = DeviceRegistry()
