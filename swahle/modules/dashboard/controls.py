"""Device control primitives, shared by the dashboard router and the smart
mirror app.

The logic used to live inline in ``router.py``; it moved here so the mirror
app (a separate process on its own LAN port) drives the exact same code path
and parity can't drift. These return plain dicts / raise -- the HTTP shaping
stays in each caller's router.
"""
from __future__ import annotations

import asyncio
import logging

from app.settings import settings

from .pump_alert import pump_alert
from .services import registry

log = logging.getLogger("swahle.dashboard.controls")


async def maybe_pump_alert(device_id: str, state: dict) -> None:
    """Keep the pump-run alert (and the mirror pump icon) in step with an
    app-initiated pump relay change."""
    if device_id == settings.pump_alert_pump and "on" in state:
        await pump_alert.on_event(bool(state["on"]))


async def device_snapshots(timeout: float | None = None) -> list[dict]:
    """Every registered device with its current state, fetched concurrently.
    A device that fails to answer (or exceeds ``timeout`` seconds, if given) is
    returned with its cached state and ``reachable: False`` rather than
    stalling the whole batch on one dead host."""
    async def snap(dev):
        try:
            call = dev.state()
            raw = await (asyncio.wait_for(call, timeout) if timeout else call)
            state = {**raw, "reachable": True}
        except (Exception, asyncio.TimeoutError) as exc:  # noqa: BLE001
            log.warning("device %s state failed: %s", dev.id, exc)
            cached = dev.cached_state() if hasattr(dev, "cached_state") else {}
            state = {**cached, "reachable": False}
        return {"id": dev.id, "kind": dev.kind, "state": state}

    return list(await asyncio.gather(*[snap(d) for d in registry.all()]))


async def set_device(device_id: str, *, notify_pump_alert: bool = True, **attrs) -> dict:
    """Apply attributes (on / color / brightness / …). Raises ``KeyError`` for
    an unknown id, ``ValueError``/``TypeError`` for a bad attr value."""
    dev = registry.get(device_id)  # raises KeyError
    state = await dev.set(**attrs)
    if notify_pump_alert:
        await maybe_pump_alert(device_id, state)
    return {**state, "reachable": True}


async def toggle_device(device_id: str, *, notify_pump_alert: bool = True) -> dict:
    dev = registry.get(device_id)  # raises KeyError
    state = await dev.toggle()
    if notify_pump_alert:
        await maybe_pump_alert(device_id, state)
    return {**state, "reachable": True}
