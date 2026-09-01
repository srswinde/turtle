"""cameras module (web side): private live-snapshot views for both cameras.

The heavy ingest loop runs as a separate process (worker.py); the web app only
exposes a live snapshot proxy + a live-view page here, so it imports ``camera``
(amcrest) but never torch.

Live video (2026-08-23): ``/app/live/{cam}/{path}`` reverse-proxies to MediaMTX
(``settings.mediamtx_url``), which pulls each camera's RTSP sub-stream and
remuxes it to HLS (video + audio, no re-encode). The browser never talks to
MediaMTX directly -- this keeps it behind the same require_user/Cloudflare
Access gate as everything else, with no new tunnel config. HLS playback is
many small sequential HTTP requests (master playlist -> media playlist ->
segments), and MediaMTX's playlist responses 302-redirect to a session-scoped
URL, so this is a real (if minimal) streaming proxy, not a single forwarded
call: redirects are relayed with a rewritten Location so the browser's next
request comes back through this same proxy prefix instead of trying to reach
MediaMTX (an internal-only host) directly.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.deps import require_user
from app.settings import settings
from app.templating import templates
from shared.module import BaseModule, NavItem

from .camera import Camera

router = APIRouter(prefix="/app", tags=["private"], dependencies=[Depends(require_user)])

_proxy_client = httpx.AsyncClient(follow_redirects=False, timeout=15.0)

_cameras: dict[str, Camera] = {}


def _get_camera(name: str) -> Camera:
    if name not in settings.cameras:
        raise KeyError(name)
    if name not in _cameras:
        _cameras[name] = Camera(settings.cameras[name])
    return _cameras[name]


@router.get("/cameras", response_class=HTMLResponse)
async def cameras_page(request: Request):
    return templates.TemplateResponse(request, "cameras.html",
                                      {"cameras": list(settings.cameras.keys())})


@router.get("/api/cameras/{name}/snapshot")
async def snapshot(name: str):
    try:
        camera = _get_camera(name)
    except KeyError:
        return JSONResponse({"error": f"Unknown camera: {name}"}, status_code=404)
    try:
        data = await asyncio.to_thread(camera.snapshot_bytes)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"camera unavailable: {exc}"}, status_code=503)
    return Response(content=data, media_type="image/jpeg")


# --- Live video (HLS via MediaMTX) ------------------------------------------

# Headers that don't make sense (or actively break things) to relay verbatim
# in either direction of a streamed proxy response.
_HOP_BY_HOP = {"content-length", "content-encoding", "transfer-encoding", "connection"}


@router.get("/live/{cam}/{path:path}")
async def live_proxy(cam: str, path: str, request: Request):
    if cam not in settings.cameras:
        return JSONResponse({"error": f"Unknown camera: {cam}"}, status_code=404)
    if not settings.mediamtx_url:
        return JSONResponse({"error": "live video not configured (set SWAHLE_MEDIAMTX_URL)"},
                            status_code=503)

    upstream_url = f"{settings.mediamtx_url.rstrip('/')}/{cam}/{path}"
    # MediaMTX's HLS handler does a cookie-support check (302 + Set-Cookie,
    # confirmed via curl 2026-08-23: first request has no cookie -> redirect;
    # media-playlist/segment requests without the cookie 401) before serving
    # anything past the master playlist -- relay the browser's cookie upstream
    # or every follow-up request fails.
    fwd_headers = {}
    if "cookie" in request.headers:
        fwd_headers["cookie"] = request.headers["cookie"]

    try:
        upstream = await _proxy_client.get(upstream_url, params=request.query_params,
                                           headers=fwd_headers)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"live video unavailable: {exc}"}, status_code=503)

    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    if upstream.is_redirect:
        # MediaMTX's own redirect Location is path-only (e.g.
        # "/hole/index.m3u8?cookieCheck=1", confirmed via curl -- not an
        # absolute URL) -- rewrite the /{cam}/ prefix it uses for itself back
        # to our /app/live/{cam}/ prefix so the browser's follow-up request
        # comes back through this proxy instead of trying to reach MediaMTX
        # (an internal-only host) directly. Handle an absolute-URL form too
        # in case that ever changes upstream.
        location = upstream.headers.get("location", "")
        mtx_path_prefix = f"/{cam}/"
        upstream_abs_prefix = f"{settings.mediamtx_url.rstrip('/')}/{cam}/"
        if location.startswith(upstream_abs_prefix):
            location = f"/app/live/{cam}/" + location[len(upstream_abs_prefix):]
        elif location.startswith(mtx_path_prefix):
            location = f"/app/live/{cam}/" + location[len(mtx_path_prefix):]
        headers["location"] = location
        return Response(status_code=upstream.status_code, headers=headers)

    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=headers, media_type=upstream.headers.get("content-type"))


@router.get("/live", response_class=HTMLResponse)
async def live_page(request: Request):
    return templates.TemplateResponse(request, "live.html",
                                      {"cameras": list(settings.cameras.keys())})


class CamerasModule(BaseModule):
    name = "cameras"

    def get_routers(self) -> list[APIRouter]:
        return [router]

    def nav(self) -> list[NavItem]:
        return [
            NavItem("Live cameras", "/app/cameras",
                    "Live hole & shed camera snapshots.", "\U0001F4F7", "private"),
            NavItem("Live video", "/app/live",
                    "Live video + audio from the hole & shed cameras.", "\U0001F3A5", "private"),
        ]
