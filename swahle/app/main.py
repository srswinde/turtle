"""Application factory.

Discovers modules, mounts their routers (public at ``/``, private at ``/app``),
aggregates their nav entries into the landing pages, and wires each module's
``startup``/``shutdown`` into the FastAPI lifespan.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.deps import require_user
from app.settings import settings
from app.templating import templates
from shared.module import Module

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("swahle")


def discover_modules() -> list[Module]:
    """Instantiate the enabled modules (explicit keeps ordering/failures obvious)."""
    from modules.cameras.module import CamerasModule
    from modules.dashboard.module import DashboardModule
    from modules.detect.module import DetectModule
    from modules.naturalist.module import NaturalistModule
    from modules.services.module import ServicesModule
    from modules.solar.module import SolarModule
    from modules.turtle_db.module import TurtleDbModule

    return [TurtleDbModule(), DashboardModule(), CamerasModule(), DetectModule(),
            NaturalistModule(), ServicesModule(), SolarModule()]


def create_app() -> FastAPI:
    modules = discover_modules()
    nav_items = [item for module in modules for item in module.nav()]
    public_nav = [i for i in nav_items if i.zone == "public"]
    private_nav = [i for i in nav_items if i.zone == "private"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for module in modules:
            log.info("startup: %s", module.name)
            await module.startup()
        try:
            yield
        finally:
            for module in reversed(modules):
                log.info("shutdown: %s", module.name)
                await module.shutdown()

    app = FastAPI(title="Swahle Smart Home", lifespan=lifespan)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def _site_context(page: str) -> dict:
        # Context some root-served pages need (e.g. the cameras list).
        if page == "cameras.html":
            return {"cameras": list(settings.cameras.keys())}
        return {}

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request):
        # Memorable subdomains (power.swahle.org, camera.swahle.org, ...) serve
        # their mapped page at the root; everything else gets the public landing.
        host = (request.headers.get("host") or "").split(":")[0].lower()
        page = settings.site_pages.get(host)
        if page:
            return templates.TemplateResponse(request, page, _site_context(page))
        return templates.TemplateResponse(request, "landing.html", {
            "title": "Swahle Smart Home",
            "subtitle": "Cassini the tortoise, live from the backyard.",
            "items": public_nav,
            "show_login": True,
        })

    @app.get("/app", response_class=HTMLResponse, dependencies=[Depends(require_user)])
    async def app_home(request: Request):
        return templates.TemplateResponse(request, "landing.html", {
            "title": "Swahle · Controls",
            "subtitle": "Private modules.",
            "items": private_nav,
            "show_login": False,
        })

    @app.get("/app/api/whoami", include_in_schema=False)
    async def whoami(user: str = Depends(require_user)):
        # Cheap session probe for the client-side re-auth guard: 200 when the
        # Cloudflare Access session is valid; expired sessions are 302'd to
        # login by Cloudflare before ever reaching here.
        return JSONResponse({"user": user})

    if settings.static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

    for module in modules:
        for router in module.get_routers():
            app.include_router(router)

    return app


app = create_app()
