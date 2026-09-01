"""Checkpoint registry: discovery, active-model pointer, and rollback.

No torch here — this is pure filesystem bookkeeping so the web app can manage
model versions without loading the ML stack. Checkpoints follow the existing
layout::

    <model_root>/<YYYYMMDD-HHMM>/model_<i>/model.pth

The active model is tracked by an ``ACTIVE`` pointer file; the prior value is
kept in ``ACTIVE.prev`` so activation is reversible (one-click rollback).
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from app.settings import settings

_RUN_RE = re.compile(r"(\d{8}-\d{4})")


def _root() -> Path:
    return Path(settings.model_root)


def _active_file() -> Path:
    return _root() / "ACTIVE"


def _prev_file() -> Path:
    return _root() / "ACTIVE.prev"


def active_model_path() -> str:
    """Resolve the currently-active checkpoint (pointer file or config default)."""
    ptr = _active_file()
    if ptr.is_file():
        path = ptr.read_text().strip()
        if path and Path(path).is_file():
            return path
    return settings.cassini_model_path


def _run_time(run_dir: Path) -> datetime.datetime | None:
    m = _RUN_RE.search(run_dir.name)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M")
    except ValueError:
        return None


def list_checkpoints(limit: int = 200) -> list[dict]:
    """Most-recent model.pth checkpoints, newest run first, with active marker.

    Capped at ``limit`` (there can be thousands across historical runs); the
    active model is always included even if it falls outside the cap.
    """
    root = _root()
    active = active_model_path()
    out: list[dict] = []
    if not root.is_dir():
        return out
    for run_dir in sorted(root.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        created = _run_time(run_dir)
        if created is None:
            continue
        models = sorted(
            run_dir.glob("model_*/model.pth"),
            key=lambda p: int(re.search(r"model_(\d+)", p.parent.name).group(1))
            if re.search(r"model_(\d+)", p.parent.name) else 0,
        )
        for model_path in models:
            out.append({
                "path": str(model_path),
                "run": run_dir.name,
                "model": model_path.parent.name,
                "created": created.isoformat(),
                "size": model_path.stat().st_size,
                "active": str(model_path) == active,
            })
        if len(out) >= limit:
            break

    out = out[:limit]
    # Always surface the active checkpoint, even if outside the cap.
    if active and not any(c["active"] for c in out) and Path(active).is_file():
        run_dir = Path(active).parent.parent
        created = _run_time(run_dir)
        out.append({
            "path": active,
            "run": run_dir.name,
            "model": Path(active).parent.name,
            "created": created.isoformat() if created else "",
            "size": Path(active).stat().st_size,
            "active": True,
        })
    return out


def validate(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix == ".pth" and p.stat().st_size > 0


def set_active(path: str) -> str:
    """Point the active model at ``path`` (keeping the prior value for rollback)."""
    if not validate(path):
        raise ValueError(f"Not a valid checkpoint: {path}")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    current = active_model_path()
    if current and current != path:
        _prev_file().write_text(current)
    _active_file().write_text(path)
    return path


def rollback() -> str:
    """Swap active and previous checkpoints. Returns the new active path."""
    prev = _prev_file()
    if not prev.is_file():
        raise ValueError("No previous checkpoint to roll back to")
    target = prev.read_text().strip()
    if not validate(target):
        raise ValueError(f"Previous checkpoint is invalid: {target}")
    current = active_model_path()
    _active_file().write_text(target)
    _prev_file().write_text(current)  # allow rolling forward again
    return target
