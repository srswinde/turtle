"""Live camera-image + temperature feed over a websocket.

Faithful port of the legacy ``start_redis`` broadcast loop: a single background
task subscribes to the Redis pub/sub channels the ingest side publishes to, logs
temperature readings to the DB, and fans messages out to connected websocket
clients. Lives in the private zone (live views are not public).

Resilient to Redis being unavailable (e.g. in dev before the ingest worker is
ported): the loop retries instead of crashing the app.
"""
from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.deps import require_user
from app.settings import settings
from shared.db import SessionLocal
from shared.models import conditions
from shared.staticfiles import serve_file

log = logging.getLogger("swahle.turtle_db.live")

DISK_ROOT = "/mnt/turtle"
LIVE_URL_PREFIX = "/app/live-static"


def _log_conditions(data: dict) -> None:
    """Persist a temperature/humidity reading (mirrors legacy log_conditions)."""
    session = SessionLocal()
    try:
        session.add(
            conditions(
                timestamp=data["timestamp"],
                temperature_F=data["temp"],
                relative_humidity=data.get("humid"),
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - never let logging kill the feed
        session.rollback()
        log.exception("Failed to log conditions")
    finally:
        session.close()


class LiveHub:
    """Owns websocket connections and the Redis subscription task."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="live-redis")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _broadcast(self, message: dict) -> None:
        dead = []
        payload = json.dumps(message)
        for ws in self._clients:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def _handle(self, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode())
        except (ValueError, AttributeError):
            return

        if "temp" in data:
            # DB write is blocking; keep it off the event loop.
            await asyncio.to_thread(_log_conditions, data)
            await self._broadcast({"temp": data})
        elif "name" in data:
            data["name"] = data["name"].replace(DISK_ROOT, LIVE_URL_PREFIX)
            name = data["name"]
            if "backyard" in name:
                await self._broadcast({"backyard_image": data})
            elif "tanspot" in name:
                await self._broadcast({"tanspot_image": data})
            else:
                await self._broadcast({"home_image": data})

    async def _run(self) -> None:
        """Subscribe and forward forever, reconnecting on failure."""
        while True:
            try:
                client = aioredis.Redis(
                    host=settings.redis_host, port=settings.redis_port
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(*settings.redis_channels)
                log.info("Subscribed to %s", settings.redis_channels)
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    await self._handle(msg["data"])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("Live feed Redis error; retrying in 5s", exc_info=True)
                await asyncio.sleep(5)


hub = LiveHub()


def build_router() -> APIRouter:
    router = APIRouter(prefix="/app", tags=["private"])

    @router.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await hub.connect(ws)
        try:
            while True:
                # We only push; keep the socket open and drain client pings.
                await ws.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(ws)
        except Exception:  # noqa: BLE001
            hub.disconnect(ws)

    @router.get("/live-static/{path:path}", dependencies=[Depends(require_user)])
    async def live_static(path: str):
        return serve_file(settings.live_img_root, path)

    return router
