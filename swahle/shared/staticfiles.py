"""Safe single-file serving from a fixed root, with a path-traversal guard.

Shared by every zone that serves images off the RAID mounts so the traversal
check lives in exactly one place.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse, Response


def serve_file(root: Path, path: str) -> Response:
    target = (root / path).resolve()
    root = root.resolve()
    if root not in target.parents and target != root:
        return JSONResponse({"detail": "not found"}, status_code=404)
    if not target.is_file():
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(target)
