"""Shared FastAPI dependencies.

``require_user`` is the in-app half of the auth model. Cloudflare Access is the
primary gate in front of the tunnel; on authenticated requests it injects a
signed JWT in the ``Cf-Access-Jwt-Assertion`` header. This dependency verifies
that JWT against Cloudflare's public keys and returns the user's email.

In dev (no CF team domain configured, ``dev_bypass_auth`` on) it returns a
placeholder identity so ``/app/*`` is reachable locally without Cloudflare.
"""
from __future__ import annotations

import time

import httpx
import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from app.settings import settings

DEV_USER = "dev@localhost"

_jwk_client: PyJWKClient | None = None


def _certs_url() -> str:
    return f"https://{settings.cf_access_team_domain}/cdn-cgi/access/certs"


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(_certs_url())
    return _jwk_client


def verify_cf_access_jwt(token: str) -> str:
    """Validate a Cloudflare Access JWT and return the user's email.

    Raises HTTPException(403) on any validation failure.
    """
    # cf_access_aud may be a comma-separated list (one AUD per Access app /
    # subdomain). The token must match at least one.
    auds = [a.strip() for a in settings.cf_access_aud.split(",") if a.strip()]
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=auds or None,
            issuer=f"https://{settings.cf_access_team_domain}",
            options={"verify_aud": bool(auds)},
        )
    except Exception as exc:  # noqa: BLE001 - any failure => forbidden
        raise HTTPException(status_code=403, detail="Invalid Access token") from exc

    if claims.get("exp", 0) < time.time():
        raise HTTPException(status_code=403, detail="Expired Access token")
    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=403, detail="No email in Access token")
    return email


async def require_user(request: Request) -> str:
    """Dependency for private (`/app`) routers."""
    token = request.headers.get("Cf-Access-Jwt-Assertion")

    if not token:
        if settings.dev_bypass_auth and not settings.cf_access_team_domain:
            return DEV_USER
        raise HTTPException(status_code=403, detail="No Access token")

    return verify_cf_access_jwt(token)
