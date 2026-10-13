"""Smart-mirror application factory (a *separate* process from ``app.main``).

Runs on its own LAN-only port (``uvicorn app.mirror_app:app --host
$SWAHLE_MIRROR_BIND_HOST --port $SWAHLE_MIRROR_PORT``), never routed through the
Cloudflare tunnel. It shares the codebase -- DB, device registry, camera
client, naturalist queries -- but not the process: mirror events reach it from
``swahle-web`` over the Redis bus (see ``shared/events.py``).

Lifespan: load persisted screen state, then start the hub's three loops
(Redis listener, TTL ticker, slideshow-pool builder).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.settings import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("swahle.mirror")


def create_mirror_app() -> FastAPI:
    from modules.mirror.hub import hub
    from modules.mirror.models import ensure_tables
    from modules.mirror.router import enroll_router, router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            ensure_tables()
        except Exception as exc:  # noqa: BLE001 - don't block boot on DDL
            log.error("mirror table ensure failed: %s", exc)
        hub.start()
        log.info("mirror app up on %s:%s", settings.mirror_bind_host, settings.mirror_port)
        try:
            yield
        finally:
            await hub.stop()
            from modules.dashboard.services import registry
            await registry.aclose()

    app = FastAPI(title="Swahle Smart Mirror", lifespan=lifespan)

    # The kiosk is one persistent Chromium tab that's "refreshed" by killing
    # and relaunching the process (see deploy/kiosk/README.md), never by a
    # user-triggered reload -- and a plain top-level navigation on process
    # start is free to serve a heuristically-"fresh" cached copy of a static
    # file without even asking the server, since neither StaticFiles nor
    # FileResponse set Cache-Control by default (only Last-Modified/ETag).
    # Confirmed via chrome://inspect (performance.getEntriesByType("resource"))
    # that this actually happened: app.css served with transferSize 0 (pure
    # cache hit) was several edits stale, which is why a "working" fix kept
    # not showing up on the panel. `no-cache` still allows a 304 round-trip
    # (cheap on this LAN, these files are tiny) but forces that revalidation
    # every time instead of trusting a local cache's guess.
    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.include_router(enroll_router)
    app.include_router(router)

    if settings.mirror_static_dir.exists():
        app.mount("/mirror-static",
                  StaticFiles(directory=str(settings.mirror_static_dir)),
                  name="mirror-static")

    return app


app = create_mirror_app()
