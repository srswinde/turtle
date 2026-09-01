"""Services module (web side): control panel for the managed standalone scripts.

Torch-free; proxies control to the host agent (settings.service_agent_url).
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.deps import require_user
from app.settings import settings
from app.templating import templates

log = logging.getLogger("swahle.services")

router = APIRouter(prefix="/app", tags=["private"], dependencies=[Depends(require_user)])


@router.get("/services", response_class=HTMLResponse)
async def services_page(request: Request):
    return templates.TemplateResponse(request, "services.html")


async def _proxy(method: str, path: str, **kw) -> JSONResponse:
    if not settings.service_agent_url:
        return JSONResponse({"error": "services agent not configured"}, status_code=503)
    url = settings.service_agent_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, url, **kw)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as exc:  # noqa: BLE001
        log.error("services agent error: %s", exc)
        return JSONResponse({"error": "services agent unreachable"}, status_code=503)


@router.get("/api/services")
async def list_services():
    return await _proxy("GET", "/services")


@router.post("/api/services/{name}/{action}")
async def control_service(name: str, action: str):
    return await _proxy("POST", f"/services/{name}/{action}")


@router.get("/api/services/{name}/logs")
async def service_logs(name: str, lines: int = 120):
    return await _proxy("GET", f"/services/{name}/logs", params={"lines": lines})
