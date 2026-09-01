"""Simple password gate for the public Aravaipa page.

Deliberately NOT Cloudflare Access / require_user — this is a public-zone page
guarded by a single shared password, per request. A signed cookie proves the
visitor supplied the password once; there's no session store or user identity,
just a stateless HMAC check.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from app.settings import settings

COOKIE_NAME = "aravaipa_auth"
_TOKEN_PAYLOAD = b"aravaipa-ok"


def _secret() -> bytes:
    return (settings.aravaipa_cookie_secret or settings.aravaipa_password).encode()


def make_token() -> str:
    return hmac.new(_secret(), _TOKEN_PAYLOAD, hashlib.sha256).hexdigest()


def check_password(password: str) -> bool:
    if not settings.aravaipa_password:
        return False
    return hmac.compare_digest(password, settings.aravaipa_password)


def is_authenticated(request: Request) -> bool:
    if not settings.aravaipa_password:
        return False
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    return hmac.compare_digest(token, make_token())
