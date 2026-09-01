"""Device protocol.

A uniform interface over smart-home devices so the dashboard UI never has to
know device-specific details (Shelly RPC, WLED JSON, etc.). Concrete
implementations live alongside in ``shelly.py`` / ``wled.py``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Device(Protocol):
    id: str
    kind: str  # "shelly" | "wled" | ...

    async def state(self) -> dict:
        """Return current device state (always includes ``on``)."""
        ...

    async def toggle(self) -> dict:
        """Flip on/off; return the resulting state."""
        ...

    async def set(self, **kwargs) -> dict:
        """Apply attributes (on, color, brightness, …); return the state."""
        ...
