"""Disk-path <-> mirror-URL mapping.

The mirror app serves camera frames off the RAID mounts under its own
``/mirror/img/...`` prefix (the main web app's ``/hole-cam/static`` /
``/app/shed-cam/images`` routes live behind Cloudflare Access and aren't
reachable from this process). These helpers keep raw paths server-side and
rewrite the URLs the reused naturalist helpers already produce.
"""
from __future__ import annotations

from app.settings import settings

HOLE_URL = "/mirror/img/hole/"
SHED_URL = "/mirror/img/shed/"

# Prefixes the reused naturalist helpers emit (sessionize._path_to_url /
# naturalist.models._shed_path_to_url).
_HOLE_SRC = "/hole-cam/static/"
_SHED_SRC = "/app/shed-cam/images/"


def hole_path_to_url(path: str | None) -> str:
    if not path:
        return ""
    root = str(settings.hole_cam_dir).rstrip("/") + "/"
    return HOLE_URL + path[len(root):] if path.startswith(root) else ""


def shed_path_to_url(path: str | None) -> str:
    if not path:
        return ""
    root = str(settings.shed_cam_dir).rstrip("/") + "/"
    return SHED_URL + path[len(root):] if path.startswith(root) else ""


def rewrite(url: str | None) -> str:
    """Rewrite a main-web image URL to this app's prefix."""
    if not url:
        return ""
    if url.startswith(_HOLE_SRC):
        return HOLE_URL + url[len(_HOLE_SRC):]
    if url.startswith(_SHED_SRC):
        return SHED_URL + url[len(_SHED_SRC):]
    return url


def image_ref_to_url(image: dict | None) -> str:
    """Resolve a bus event's ``{"kind","ref"}`` image to a served URL. ``ref``
    is an absolute disk path for hole/shed, or a camera name for ``live``."""
    if not image:
        return ""
    kind, ref = image.get("kind"), image.get("ref") or ""
    if kind == "live":
        return f"/mirror/img/live/{ref or 'hole'}"
    if kind == "hole":
        return hole_path_to_url(ref)
    if kind == "shed":
        return shed_path_to_url(ref)
    return ""
