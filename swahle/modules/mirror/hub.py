"""SSE fan-out hub for the mirror app.

Owns the set of connected browser queues, the Redis bus listener that folds
events into :data:`state`, and the ticker that expires takeovers / prunes the
icon tray. Every state change is pushed to every client as a full snapshot
(plus ``last_event`` for animation), so a client that reconnects is always
correct without replaying anything.
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.settings import settings
from shared.events import bus

from .state import state

log = logging.getLogger("swahle.mirror.hub")


class MirrorHub:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []

    # --- client plumbing ----------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def broadcast(self, message: dict) -> None:
        payload = json.dumps(message)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow client -- drop it; its EventSource will reconnect and
                # GET /mirror/state to resync.
                self._subs.discard(q)

    def broadcast_state(self) -> None:
        self.broadcast({"type": "state", **state.snapshot()})

    # --- background loops -------------------------------------------------

    async def _run_bus(self) -> None:
        async for event in bus.listen():
            try:
                state.apply(event)
                state.save()
                self.broadcast_state()
            except Exception:  # noqa: BLE001 - never let one bad event kill the loop
                log.exception("failed handling mirror event")

    async def _run_ticker(self) -> None:
        while True:
            await asyncio.sleep(settings.mirror_tick_seconds)
            try:
                if state.tick():
                    state.save()
                    self.broadcast_state()
            except Exception:  # noqa: BLE001
                log.exception("mirror ticker failed")

    async def _run_pool(self) -> None:
        from .slides import build_pool

        while True:
            try:
                await build_pool()
            except Exception:  # noqa: BLE001
                log.exception("slide pool build failed")
            await asyncio.sleep(settings.mirror_slide_rebuild_seconds)

    def start(self) -> None:
        state.load()
        self._tasks = [
            asyncio.create_task(self._run_bus(), name="mirror-bus"),
            asyncio.create_task(self._run_ticker(), name="mirror-ticker"),
            asyncio.create_task(self._run_pool(), name="mirror-pool"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks = []


hub = MirrorHub()
