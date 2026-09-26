"""Dashboard routes (private zone).

Mounted at ``/app`` behind ``require_user``. Serves the dashboard page and the
control/data API the frontend calls (Shelly light/pump, WLED strips, hole/shed
camera data, Cassini decisions).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.deps import require_user
from app.settings import settings
from app.templating import templates
from shared.models import HAS_TURTLE
from shared.staticfiles import serve_file

from .controls import device_snapshots, maybe_pump_alert as _maybe_pump_alert
from .controls import set_device, toggle_device
from .pump_alert import pump_alert
from .services import hole_manager, registry, shed_manager

log = logging.getLogger("swahle.dashboard")

router = APIRouter(prefix="/app", tags=["private"], dependencies=[Depends(require_user)])

# Public (unauthenticated, LAN) router for device webhooks -> this app.
hooks_router = APIRouter(tags=["hooks"])


@hooks_router.api_route("/hooks/pump", methods=["GET", "POST"], name="pump_hook")
async def pump_hook(state: str = "", token: str = ""):
    if token != settings.hook_token:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await pump_alert.on_event(state.lower() == "on")
    return JSONResponse({"ok": True, "pump": state})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/controls", response_class=HTMLResponse)
async def controls_page(request: Request):
    return templates.TemplateResponse(request, "controls.html")


# --- Cassini (hole-cam recent detection) -----------------------------------

async def _cassini_response() -> Response:
    detection = hole_manager.recent_detection()
    if detection is None:
        try:
            await hole_manager.refresh()
        except Exception as exc:  # noqa: BLE001
            log.error("cassini refresh failed: %s", exc)
            return JSONResponse({"error": "Cassini data unavailable"}, status_code=503)
        detection = hole_manager.recent_detection()
    if detection is None:
        return Response(status_code=204)
    return JSONResponse({"detection": detection})


@router.get("/api/cassini")
async def cassini_get():
    return await _cassini_response()


@router.post("/api/cassini")
async def cassini_post():
    try:
        await hole_manager.refresh()
    except Exception as exc:  # noqa: BLE001
        log.error("cassini refresh failed: %s", exc)
        return JSONResponse({"error": "Cassini data unavailable"}, status_code=503)
    detection = hole_manager.recent_detection()
    if detection is None:
        return Response(status_code=204)
    return JSONResponse({"detection": detection})


@router.post("/api/cassini/decision")
async def cassini_decision(request: Request):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

    decision = payload.get("decision") or {}
    timestamp = decision.get("timestamp")
    has_cassini = decision.get("hasCassini")
    if timestamp is None or not isinstance(has_cassini, bool):
        return JSONResponse(
            {"error": "timestamp and hasCassini are required"}, status_code=400
        )
    try:
        await hole_manager.is_cassini(int(timestamp), has_cassini)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        log.error("failed to persist cassini decision: %s", exc)
        return JSONResponse(
            {"error": "Unable to persist cassini decision"}, status_code=500
        )
    return JSONResponse({"ok": True})


# --- Shelly relays (light + pump) ------------------------------------------

async def _relay_state(device_id: str) -> Response:
    device = registry.get(device_id)
    try:
        state = await device.state()
    except Exception as exc:  # noqa: BLE001
        log.error("%s status failed: %s", device_id, exc)
        cached = device.cached_state() if hasattr(device, "cached_state") else {"on": False}
        return JSONResponse(
            {"error": f"{device_id} status unavailable", "on": cached["on"]},
            status_code=503,
        )
    return JSONResponse({"on": state["on"]})


async def _relay_toggle(device_id: str) -> Response:
    device = registry.get(device_id)
    state = await device.toggle()
    return JSONResponse({"on": state["on"]})


@router.get("/api/light")
async def light_status():
    return await _relay_state("light")


@router.post("/api/light/toggle")
async def light_toggle():
    return await _relay_toggle("light")


@router.get("/api/pump")
async def pump_status():
    return await _relay_state("pump")


@router.post("/api/pump/toggle")
async def pump_toggle():
    return await _relay_toggle("pump")


# The BLU gateway re-POSTs the same advertisement several times per physical
# press (each advert shares one BTHome packet-id). Track the last handled pid so
# a single press toggles the pump exactly once instead of flapping.
_last_button_pid: int | None = None


async def _handle_button_payload(payload: dict) -> JSONResponse:
    """Decode a BTHome button payload → toggle pump/light (ported verbatim).

    Shared by the authenticated ``/app/api/buttons`` route and the token-gated
    ``/hooks/buttons`` webhook the LAN Shelly BLU gateway posts to.
    """
    global _last_button_pid

    # 2026-09-05: the BLU gateway sometimes posts "decoded": null for an
    # advert it couldn't parse (not just an absent key) -- `"data" not in
    # None` raised TypeError and 500'd the webhook ~4x/day. Guard the value,
    # not just its presence.
    decoded = payload.get("decoded")
    if not decoded or "data" not in decoded:
        return JSONResponse({"error": "No buttons decoded data"}, status_code=503)

    presses = []
    pid = None
    for kv in decoded["data"]:
        key = list(kv.keys())[0]
        if key == "pid":
            pid = list(kv.values())[0]
        elif key == "Button":
            presses.append(list(kv.values())[0])

    # De-dupe repeated adverts of the same press (same packet-id).
    if pid is not None and pid == _last_button_pid:
        state = await registry.get("pump").state()
        return JSONResponse({"pump_state": state["on"], "deduped": True})
    _last_button_pid = pid

    if len(presses) > 0 and presses[0] > 0:
        await registry.get("pump").toggle()
    if len(presses) > 1 and presses[1] > 0:
        await registry.get("light").toggle()

    state = await registry.get("pump").state()
    await pump_alert.on_event(bool(state["on"]))
    return JSONResponse({"pump_state": state["on"]})


@router.post("/api/buttons")
async def buttons(request: Request):
    """Hardware button decode → toggle pump/light (authenticated, /app zone)."""
    return await _handle_button_payload(await request.json())


@hooks_router.post("/hooks/buttons", name="buttons_hook")
async def buttons_hook(request: Request, token: str = ""):
    """LAN webhook for the Shelly BLU gateway (no Cloudflare Access on-device).

    Same shared-secret ``token`` pattern as ``/hooks/pump`` — the gateway can't
    present a Cloudflare Access JWT, so this stays off the authenticated router.
    """
    if token != settings.hook_token:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _handle_button_payload(await request.json())


# --- WLED strips ------------------------------------------------------------

@router.get("/api/wled/{device}")
async def wled_state(device: str):
    try:
        return JSONResponse(await registry.get(device).state())
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        log.error("WLED state failed for %s: %s", device, exc)
        return JSONResponse({"error": "WLED controller unavailable"}, status_code=503)


@router.post("/api/wled/{device}/power")
async def wled_power(device: str, request: Request):
    try:
        payload = await request.json()
        return JSONResponse(await registry.get(device).set(on=bool(payload.get("on", False))))
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.error("WLED power failed for %s: %s", device, exc)
        return JSONResponse({"error": "WLED power update failed"}, status_code=503)


@router.post("/api/wled/{device}/color")
async def wled_color(device: str, request: Request):
    try:
        payload = await request.json()
        return JSONResponse(await registry.get(device).set(color=payload.get("color")))
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.error("WLED color failed for %s: %s", device, exc)
        return JSONResponse({"error": "WLED color update failed"}, status_code=503)


# --- Generic device controls (registry-driven) ----------------------------

@router.get("/api/devices")
async def list_devices():
    """Every device with its current state, fetched concurrently."""
    return JSONResponse(await device_snapshots())


@router.post("/api/devices/{device_id}/set")
async def device_set(device_id: str, request: Request):
    body = await request.json()
    try:
        return JSONResponse(await set_device(device_id, **body))
    except KeyError:
        return JSONResponse({"error": f"Unknown device: {device_id}"}, status_code=404)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.error("device %s set failed: %s", device_id, exc)
        return JSONResponse({"error": f"{device_id} unavailable"}, status_code=503)


@router.post("/api/devices/{device_id}/toggle")
async def device_toggle(device_id: str):
    try:
        return JSONResponse(await toggle_device(device_id))
    except KeyError:
        return JSONResponse({"error": f"Unknown device: {device_id}"}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        log.error("device %s toggle failed: %s", device_id, exc)
        return JSONResponse({"error": f"{device_id} unavailable"}, status_code=503)


# --- Webhook management (Shelly device webhooks) ---------------------------

def _shelly_devices():
    return [d for d in registry.all() if getattr(d, "kind", None) == "shelly"]


@router.get("/webhooks", response_class=HTMLResponse)
async def webhooks_page(request: Request):
    return templates.TemplateResponse(request, "webhooks.html")


@router.get("/api/webhooks")
async def api_list_webhooks():
    out = []
    for d in _shelly_devices():
        try:
            hooks = await d.list_webhooks()
            reachable = True
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook list failed for %s: %s", d.id, exc)
            hooks, reachable = [], False
        out.append({"device": d.id, "reachable": reachable, "hooks": hooks})
    return JSONResponse({"devices": out, "hook_base_url": settings.hook_base_url})


@router.post("/api/webhooks/pump-alert/enable")
async def enable_pump_alert_webhooks():
    pump = registry.get(settings.pump_alert_pump)
    base = settings.hook_base_url.rstrip("/")
    tok = settings.hook_token
    try:
        for h in await pump.list_webhooks():                       # clear old ones first
            if str(h.get("name", "")).startswith("swahle-pump-alert"):
                await pump.delete_webhook(h["id"])
        await pump.create_webhook("switch.on", [f"{base}/hooks/pump?state=on&token={tok}"],
                                  name="swahle-pump-alert-on")
        await pump.create_webhook("switch.off", [f"{base}/hooks/pump?state=off&token={tok}"],
                                  name="swahle-pump-alert-off")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True})


@router.post("/api/webhooks/pump-alert/disable")
async def disable_pump_alert_webhooks():
    pump = registry.get(settings.pump_alert_pump)
    try:
        for h in await pump.list_webhooks():
            if str(h.get("name", "")).startswith("swahle-pump-alert"):
                await pump.delete_webhook(h["id"])
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True})


# --- Camera data ------------------------------------------------------------

@router.get("/api/shed-camera")
async def shed_camera_data():
    latest = await shed_manager.latest()
    if latest is None:
        try:
            latest = await shed_manager.refresh()
        except Exception as exc:  # noqa: BLE001
            log.error("shed camera refresh failed: %s", exc)
            return JSONResponse({"error": "Shed camera data unavailable"}, status_code=503)
    return JSONResponse({"records": latest.to_dict(orient="records")})


@router.get("/api/hole-camera")
async def hole_camera_data():
    latest = await hole_manager.latest()
    if latest is None:
        try:
            latest = await hole_manager.refresh()
        except Exception as exc:  # noqa: BLE001
            log.error("hole camera refresh failed: %s", exc)
            return JSONResponse({"error": "Hole camera data unavailable"}, status_code=503)

    if latest is None or latest.empty:
        return JSONResponse({"records": []})

    # One representative row per detection group, newest first.
    latest = latest.groupby(level=0).apply(lambda x: x.loc[x["path"].idxmax()])
    latest.sort_index(level=1, ascending=False, inplace=True)

    def has_turtle_int(x: Any) -> int:
        if x == HAS_TURTLE.YES:
            return 1
        if x == HAS_TURTLE.NO:
            return 0
        return -1

    latest["hasTurtle"] = latest["hasTurtle"].apply(has_turtle_int)
    return JSONResponse({"records": latest.to_dict(orient="records")})


# --- Camera image serving ---------------------------------------------------

@router.get("/hole-cam/images/{path:path}")
async def hole_cam_image(path: str):
    return serve_file(settings.hole_cam_dir, path)


@router.get("/shed-cam/images/{path:path}")
async def shed_cam_image(path: str):
    return serve_file(settings.shed_cam_dir, path)


# --- Dashboard frontend assets (api.js, dashboard.js, fonts) ----------------

@router.get("/dashboard-static/{path:path}")
async def dashboard_static(path: str):
    return serve_file(settings.dashboard_assets_dir, path)
