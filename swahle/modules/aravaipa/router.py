"""Aravaipa permit-availability routes — public zone, password-gated.

Mounted with no prefix (public), no ``require_user``/Cloudflare Access — a
simple shared-password cookie (see ``auth.py``) gates the actual data.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.settings import settings
from app.templating import templates

from .auth import COOKIE_NAME, check_password, is_authenticated, make_token
from .models import current_status, recent_events

router = APIRouter(tags=["public"])


@router.get("/aravaipa", response_class=HTMLResponse)
async def aravaipa_page(request: Request):
    return templates.TemplateResponse(request, "aravaipa.html", {
        "authed": is_authenticated(request),
        "configured": bool(settings.aravaipa_password),
    })


@router.post("/aravaipa/login")
async def aravaipa_login(request: Request):
    body = await request.json()
    if not check_password(body.get("password", "")):
        return JSONResponse({"error": "wrong password"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE_NAME, make_token(), max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite="lax")
    return resp


@router.post("/aravaipa/logout")
async def aravaipa_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/api/aravaipa/data")
async def aravaipa_data(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    permit_id = settings.aravaipa_permit_id
    months = settings.aravaipa_months
    return JSONResponse({
        "permit_id": permit_id,
        "months": months,
        "status": current_status(permit_id, months=months),
        "events": recent_events(permit_id, months=months),
        "poll_seconds": settings.aravaipa_poll_seconds,
    })
