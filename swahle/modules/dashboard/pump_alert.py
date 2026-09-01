"""Pump-run visual alert: gently pulse the shed WLED red while the pump is on.

Driven by three sources so it's instant and self-healing: the /hooks/pump
webhook (Shelly fires it on switch on/off), app-initiated pump changes (Controls
/ buttons call on_event directly), and a slow poll that reconciles actual pump
state with the alert state. All idempotent.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.settings import settings
from shared.events import CLEAR, ICON_ONLY, NEW, bus, make_event

from .services import registry

log = logging.getLogger("swahle.dashboard.pump_alert")


class PumpAlert:
    def __init__(self) -> None:
        self._active = False
        self._snapshot: dict | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._mirror_on = False   # last pump state pushed to the smart mirror

    def _light(self):
        return registry.get(settings.pump_alert_light)

    def _pump(self):
        return registry.get(settings.pump_alert_pump)

    async def start(self) -> None:
        async with self._lock:
            if self._active:
                return
            light = self._light()
            try:
                self._snapshot = await light.snapshot()
                await light.pulse(settings.pump_alert_color, settings.pump_alert_fx,
                                  settings.pump_alert_speed)
                self._active = True
                log.info("pump alert ON")
            except Exception as exc:  # noqa: BLE001
                log.error("pump alert start failed: %s", exc)

    async def stop(self) -> None:
        async with self._lock:
            if not self._active:
                return
            try:
                if self._snapshot is not None:
                    await self._light().restore(self._snapshot)
                self._active = False
                log.info("pump alert OFF")
            except Exception as exc:  # noqa: BLE001
                log.error("pump alert stop failed: %s", exc)

    async def _mirror(self, pump_on: bool) -> None:
        """Drop / clear the smart-mirror pump icon on a state transition. The
        icon carries the pump's start time so the mirror can draw a runtime
        ring. Independent of ``pump_alert_enabled`` (that only governs the
        WLED pulse); best-effort like every bus publish."""
        if not settings.mirror_producers_enabled or pump_on == self._mirror_on:
            return
        self._mirror_on = pump_on
        if pump_on:
            await bus.publish(make_event(
                type="pump", kind=ICON_ONLY, op=NEW, icon="\U0001F4A7",
                title="Water pump running", ts=int(time.time() * 1000)))
        else:
            await bus.publish(make_event(type="pump", kind=ICON_ONLY, op=CLEAR,
                                         icon="\U0001F4A7"))

    async def on_event(self, pump_on: bool) -> None:
        """Instant trigger from the webhook or an app-initiated pump change."""
        await self._mirror(pump_on)
        if not settings.pump_alert_enabled:
            return
        await (self.start() if pump_on else self.stop())

    async def sync(self) -> None:
        """Self-heal: reconcile alert + mirror state with the pump's actual state."""
        if not settings.pump_alert_enabled and not settings.mirror_producers_enabled:
            return
        try:
            on = bool((await self._pump().state()).get("on"))
        except Exception:  # noqa: BLE001 - pump unreachable; leave as-is
            return
        await self._mirror(on)
        if not settings.pump_alert_enabled:
            return
        if on and not self._active:
            await self.start()
        elif not on and self._active:
            await self.stop()

    async def _poll(self) -> None:
        while True:
            await self.sync()
            await asyncio.sleep(settings.pump_alert_poll_seconds)

    async def start_poll(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll(), name="pump-alert-poll")

    async def stop_poll(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


pump_alert = PumpAlert()
