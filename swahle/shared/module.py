"""The uniform module contract.

Every feature under ``modules/`` implements this so that adding or porting a
module is mostly boilerplate. ``app/main.py`` discovers modules, mounts their
routers, aggregates their nav entries, and wires startup/shutdown into the
FastAPI lifespan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fastapi import APIRouter


@dataclass(frozen=True)
class NavItem:
    """A navigation entry a module contributes to a landing page.

    ``zone`` decides where it shows: ``public`` entries appear on the public
    landing at ``/``; ``private`` entries appear on the ``/app`` hub (behind
    Cloudflare Access).
    """
    title: str
    href: str
    description: str = ""
    icon: str = "\U0001F539"   # emoji shown on the card
    zone: str = "private"      # "public" | "private"


@runtime_checkable
class Module(Protocol):
    name: str

    def get_routers(self) -> list[APIRouter]:
        """Return the routers to mount.

        Public routers carry no prefix (mounted at ``/``); private routers use
        the ``/app`` prefix and depend on ``require_user`` (defense-in-depth
        behind Cloudflare Access).
        """
        ...

    def nav(self) -> list[NavItem]:
        """Navigation entries this module contributes (empty by default)."""
        ...

    async def startup(self) -> None:
        """Open connections, load models, start background tasks."""
        ...

    async def shutdown(self) -> None:
        """Clean shutdown of anything opened in startup."""
        ...


class BaseModule:
    """Convenience base so modules only override what they need."""

    name: str = "unnamed"

    def get_routers(self) -> list[APIRouter]:
        return []

    def nav(self) -> list[NavItem]:
        return []

    async def startup(self) -> None:  # pragma: no cover - default no-op
        return None

    async def shutdown(self) -> None:  # pragma: no cover - default no-op
        return None
