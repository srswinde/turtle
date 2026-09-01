"""Cross-process event bus for the smart-mirror app.

The event *producers* (naturalist shed/hole sighting watchers, the pump alert)
run inside the main ``swahle-web`` process; the mirror app (``app.mirror_app``)
is a separate process on its own LAN-only port. So the bus rides Redis pub/sub:
producers call :func:`publish`, the mirror app's SSE hub consumes :func:`listen`.

Faithful sibling of ``modules/turtle_db/live.py`` (LiveHub) -- same reconnecting
pub/sub loop, resilient to Redis being down (publish is best-effort and never
raises into a detection code path; the listener retries).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from app.settings import settings

log = logging.getLogger("swahle.events")

# Event `kind`
TAKEOVER = "takeover"      # commandeers the whole screen for `screen_ttl` seconds
ICON_ONLY = "icon_only"    # just drops/updates a tray icon, no screen change

# Event `op`
NEW = "new"                # a fresh occurrence
CLEAR = "clear"            # retire this type's tray icon now (e.g. pump turned off)


def make_event(
    *,
    type: str,
    kind: str = TAKEOVER,
    op: str = NEW,
    title: str = "",
    subtitle: str = "",
    icon: str = "\U0001F538",
    image: dict[str, str] | None = None,
    ts: int | None = None,
    screen_ttl: int | None = None,
) -> dict[str, Any]:
    """Build a bus event.

    ``image`` is ``{"kind": "shed"|"hole"|"live", "ref": <path|id|camera>}`` and
    is resolved to a URL by the mirror app's ``/mirror/img`` route so the raw
    disk paths never leave the server.
    """
    return {
        "id": uuid.uuid4().hex,
        "type": type,
        "kind": kind,
        "op": op,
        "title": title,
        "subtitle": subtitle,
        "icon": icon,
        "image": image,
        "ts": ts if ts is not None else int(time.time() * 1000),
        "screen_ttl": screen_ttl if screen_ttl is not None else settings.mirror_takeover_ttl,
    }


class EventBus:
    """Publish side (swahle-web) and subscribe side (mirror app) of one channel."""

    def __init__(self, channel: str | None = None) -> None:
        self._channel = channel or settings.mirror_event_channel
        self._pub: aioredis.Redis | None = None

    def _client(self) -> aioredis.Redis:
        if self._pub is None:
            self._pub = aioredis.Redis(
                host=settings.redis_host, port=settings.redis_port
            )
        return self._pub

    async def publish(self, event: dict[str, Any]) -> None:
        """Best-effort fan-out. A Redis outage must never break a caller (these
        are detection code paths), so failures are logged and swallowed."""
        try:
            await self._client().publish(self._channel, json.dumps(event))
        except Exception as exc:  # noqa: BLE001 - never propagate into producers
            log.warning("mirror event publish failed (%s): %s", event.get("type"), exc)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events forever, reconnecting on any Redis failure."""
        while True:
            try:
                client = aioredis.Redis(
                    host=settings.redis_host, port=settings.redis_port
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(self._channel)
                log.info("mirror event bus subscribed to %s", self._channel)
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        yield json.loads(msg["data"])
                    except (ValueError, TypeError):
                        log.warning("dropping unparseable mirror event")
            except Exception:  # noqa: BLE001
                log.warning("mirror event bus error; retrying in 5s", exc_info=True)
                import asyncio

                await asyncio.sleep(5)

    async def aclose(self) -> None:
        if self._pub is not None:
            await self._pub.aclose()
            self._pub = None


bus = EventBus()
