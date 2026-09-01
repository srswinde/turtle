"""Optional device gate for the mirror app.

The real control is that this process listens only on the LAN (``mirror_bind_host``)
and has no Cloudflare tunnel route. ``mirror_token`` adds a second check for
other devices on the same network: the mirror enrolls once at
``/mirror/enroll?token=...`` which drops a long-lived signed cookie; every
other route then requires it.

Stateless: the cookie value is ``HMAC(token, "swahle-mirror-v1")`` so there's
nothing to store and no extra dependency.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from app.settings import settings

COOKIE_NAME = "mirror_auth"
_COOKIE_MSG = b"swahle-mirror-v1"
_TEN_YEARS = 10 * 365 * 24 * 3600


def expected_cookie() -> str:
    return hmac.new(settings.mirror_token.encode(), _COOKIE_MSG, hashlib.sha256).hexdigest()


def token_ok(token: str) -> bool:
    return bool(settings.mirror_token) and hmac.compare_digest(token, settings.mirror_token)


def set_cookie(response) -> None:
    response.set_cookie(
        COOKIE_NAME, expected_cookie(), max_age=_TEN_YEARS,
        httponly=True, samesite="lax", secure=False,  # LAN http://<ip>:8800
    )


async def require_mirror(request: Request) -> None:
    if not settings.mirror_token:
        return  # gate disabled -- LAN isolation only
    cookie = request.cookies.get(COOKIE_NAME, "")
    if not hmac.compare_digest(cookie, expected_cookie()):
        raise HTTPException(status_code=403, detail="mirror not enrolled")
