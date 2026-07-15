"""Solar telemetry routes: LAN webhook (Shelly BLU gateway) + private read API."""
from __future__ import annotations

import base64
import dataclasses
import logging
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.deps import require_user
from app.settings import settings
from app.templating import templates

from .models import latest_reading, readings_since, save_reading
from .victron import VictronDecodeError, decrypt_manufacturer_data, parse_solar_charger

log = logging.getLogger("swahle.solar")

router = APIRouter(prefix="/app", tags=["private"], dependencies=[Depends(require_user)])

# Public (unauthenticated, LAN) router for the Shelly BLU gateway's webhook.
hooks_router = APIRouter(tags=["hooks"])


def _device_for_mac(mac: str) -> str | None:
    mac = mac.lower()
    for device_id, cfg in settings.solar_devices.items():
        if cfg.get("mac", "").lower() == mac:
            return device_id
    return None


def _manufacturer_data(raw: bytes) -> bytes | None:
    """Pull the 0xFF (Manufacturer Specific Data) AD structure out of a raw advert."""
    i = 0
    mfg = None
    while i < len(raw):
        length = raw[i]
        if length == 0 or i + 1 + length > len(raw):
            break
        ad_type, value = raw[i + 1], raw[i + 2:i + 1 + length]
        if ad_type == 0xFF:
            mfg = value
        i += 1 + length
    return mfg


@hooks_router.post("/hooks/solar", name="solar_hook")
async def solar_hook(request: Request, token: str = ""):
    """LAN webhook: the Shelly BLU gateway posts the still-encrypted BLE advert here."""
    if token != settings.hook_token:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    payload = await request.json()
    mac = str(payload.get("addr", ""))
    device_id = _device_for_mac(mac)
    if device_id is None:
        return JSONResponse({"error": f"unknown solar device: {mac}"}, status_code=404)

    adv_b64 = payload.get("adv_b64")
    if not adv_b64:
        return JSONResponse({"error": "missing adv_b64"}, status_code=400)

    mfg = _manufacturer_data(base64.b64decode(adv_b64))
    if mfg is None:
        return JSONResponse({"error": "no manufacturer data in advertisement"}, status_code=400)

    key = bytes.fromhex(settings.solar_devices[device_id]["aes_key"])
    try:
        plaintext = decrypt_manufacturer_data(mfg, key)
        model_id = mfg[3] | (mfg[4] << 8)
        nonce = mfg[7] | (mfg[8] << 8)
        reading = parse_solar_charger(plaintext, model_id=model_id, nonce=nonce)
    except VictronDecodeError as exc:
        log.warning("solar decode failed for %s: %s", device_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=422)

    entry = dataclasses.asdict(reading)
    entry["rssi"] = payload.get("rssi")
    stored = await run_in_threadpool(save_reading, device_id, entry)
    return JSONResponse({"ok": True, "reading": stored})


@router.get("/solar", response_class=HTMLResponse)
async def solar_page(request: Request):
    return templates.TemplateResponse(request, "solar.html", {
        "devices": settings.solar_devices,
    })


@router.get("/api/solar/latest")
async def solar_latest(device: str = "mppt"):
    reading = await run_in_threadpool(latest_reading, device)
    if reading is None:
        return JSONResponse({"error": f"no readings for {device}"}, status_code=404)
    return JSONResponse(reading)


@router.get("/api/solar/history")
async def solar_history(device: str = "mppt", hours: float = Query(default=24.0, gt=0, le=24 * 30)):
    since_ms = int((time.time() - hours * 3600) * 1000)
    rows = await run_in_threadpool(readings_since, device, since_ms)
    return JSONResponse({"device": device, "hours": hours, "readings": rows})
