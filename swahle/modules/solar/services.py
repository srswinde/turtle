"""Module-level singleton shared between the router and the module lifecycle."""
from __future__ import annotations

from app.settings import settings

from .store import SolarStore

solar_store = SolarStore(settings.solar_log_path)
