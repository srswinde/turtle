"""All mirror-app HTTP routes.

Page + SSE + resync + slideshow + per-view APIs (cameras / controls / cassini)
+ image proxies + enrollment. Everything except ``/mirror/enroll`` sits behind
:func:`require_mirror` (a no-op unless ``mirror_token`` is set).
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.concurrency import run_in_threadpool

from app.settings import settings
from shared.staticfiles import serve_file

from .auth import require_mirror, set_cookie, token_ok
from .cassini import day_view
from .hub import hub
from .slides import current_pool
from .state import state

log = logging.getLogger("swahle.mirror.router")

router = APIRouter(dependencies=[Depends(require_mirror)])
# Enrollment must be reachable *before* the cookie exists, so it can't carry
# the require_mirror dependency.
enroll_router = APIRouter()

_INDEX = settings.mirror_static_dir / "mirror.html"

# One Camera per name, built lazily (amcrest construction is non-blocking).
_cameras: dict = {}


def _camera(name: str):
    if name not in _cameras:
        from modules.cameras.camera import Camera
        _cameras[name] = Camera(settings.cameras[name])
    return _cameras[name]


# --- page + enrollment ----------------------------------------------------

@router.get("/", include_in_schema=False)
async def index():
    return FileResponse(_INDEX)


@enroll_router.get("/mirror/enroll", include_in_schema=False)
async def enroll(token: str = ""):
    if not token_ok(token):
        return JSONResponse({"error": "bad token"}, status_code=403)
    resp = RedirectResponse("/", status_code=303)
    set_cookie(resp)
    return resp


# --- realtime -----------------------------------------------------------

@router.get("/mirror/events", include_in_schema=False)
async def events(request: Request):
    q = hub.subscribe()

    async def stream():
        # Prime the client with the current picture.
        yield f"data: {json.dumps({'type': 'state', **state.snapshot()})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keep the Chromium/proxy connection warm
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/mirror/state")
async def get_state():
    return JSONResponse(state.snapshot())


@router.post("/mirror/dismiss")
async def dismiss():
    if state.dismiss():
        state.save()
        hub.broadcast_state()
    return JSONResponse({"ok": True})


@router.post("/mirror/icons/{etype}/open")
async def open_icon(etype: str):
    if state.open_icon(etype):
        state.save()
        hub.broadcast_state()
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "no such icon"}, status_code=404)


@router.post("/mirror/icons/{etype}/clear")
async def clear_icon(etype: str):
    if state.clear_icon(etype):
        state.save()
        hub.broadcast_state()
    return JSONResponse({"ok": True})


@router.post("/mirror/feedback/false-positive")
async def false_positive(request: Request):
    """Viewer says the active takeover isn't Cassini: record it for training,
    then clear the takeover + that event type's tray icon."""
    from .feedback import SIGHTING_TYPES, record_false_positive

    event = await request.json()
    result = await run_in_threadpool(record_false_positive, event)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    # "not Cassini" -> no tortoise anywhere: clear every sighting icon, not just
    # this event's. (The pump icon and other non-sighting icons stay.)
    changed = state.dismiss()
    for t in SIGHTING_TYPES:
        changed = state.clear_icon(t) or changed
    if changed:
        state.save()
        hub.broadcast_state()
    return JSONResponse(result)


# --- slideshow + images ------------------------------------------------

@router.get("/mirror/api/slideshow")
async def slideshow():
    slides = await run_in_threadpool(current_pool)
    return JSONResponse({
        "slides": slides,
        "live_every": settings.mirror_slide_live_every,
        "live_url": "/mirror/img/live/hole",
    })


@router.get("/mirror/img/live/{name}", include_in_schema=False)
async def live_image(name: str):
    if name not in settings.cameras:
        return JSONResponse({"error": "unknown camera"}, status_code=404)
    try:
        data = await asyncio.to_thread(_camera(name).snapshot_bytes)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"camera unavailable: {exc}"}, status_code=503)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/mirror/img/hole/{path:path}", include_in_schema=False)
async def hole_image(path: str):
    return serve_file(settings.hole_cam_dir, path)


@router.get("/mirror/img/shed/{path:path}", include_in_schema=False)
async def shed_image(path: str):
    return serve_file(settings.shed_cam_dir, path)


# --- per-view data ----------------------------------------------------

@router.get("/mirror/api/cameras")
async def cameras():
    return JSONResponse({
        "cameras": [
            {"name": n, "url": f"/mirror/img/live/{n}"} for n in settings.cameras
        ]
    })


@router.get("/mirror/api/controls")
async def controls():
    from modules.dashboard.controls import device_snapshots
    # cap per-device wait so one dead light (e.g. the bookcase WLED) doesn't
    # stall the whole page for its full HTTP timeout.
    return JSONResponse({"devices": await device_snapshots(timeout=1.2)})


@router.post("/mirror/api/controls/{device_id}/toggle")
async def controls_toggle(device_id: str):
    from modules.dashboard.controls import toggle_device
    try:
        return JSONResponse(await toggle_device(device_id, notify_pump_alert=False))
    except KeyError:
        return JSONResponse({"error": f"unknown device: {device_id}"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{device_id} unavailable: {exc}"}, status_code=503)


@router.post("/mirror/api/controls/{device_id}/set")
async def controls_set(device_id: str, request: Request):
    from modules.dashboard.controls import set_device
    body = await request.json()
    try:
        return JSONResponse(await set_device(device_id, notify_pump_alert=False, **body))
    except KeyError:
        return JSONResponse({"error": f"unknown device: {device_id}"}, status_code=404)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"{device_id} unavailable: {exc}"}, status_code=503)


@router.get("/mirror/api/cassini")
async def cassini(date: str | None = Query(default=None)):
    try:
        return JSONResponse(await run_in_threadpool(day_view, date))
    except ValueError:
        return JSONResponse({"error": "date must be YYYY-MM-DD"}, status_code=400)


@router.get("/mirror/api/ambient")
async def ambient():
    from .ambient import ambient_payload
    return JSONResponse(await ambient_payload())


@router.get("/mirror/api/forecast")
async def forecast():
    from .forecast import five_day
    return JSONResponse({"days": await five_day() or []})
