"""Shared Jinja2 templates instance.

Kept in its own module so feature modules can render templates without importing
``app.main`` (which would create an import cycle).
"""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.settings import settings

templates = Jinja2Templates(directory=str(settings.templates_dir))
