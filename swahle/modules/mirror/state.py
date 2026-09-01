"""Mirror screen state: the current takeover and the tray of event icons.

One process-wide instance (``state``) owned by the mirror app. Every mutation
persists to ``settings.mirror_state_path`` (local disk, not NFS) so an 8-hour
icon tray survives a restart. All times are epoch-ms.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.settings import settings
from shared.events import CLEAR, TAKEOVER

from .paths import image_ref_to_url

log = logging.getLogger("swahle.mirror.state")


def _now() -> int:
    return int(time.time() * 1000)


class MirrorState:
    def __init__(self) -> None:
        self.active: dict[str, Any] | None = None      # {"event": {...}, "expires": ms}
        self.icons: dict[str, dict[str, Any]] = {}     # type -> {"event": {...}, "created": ms}
        self.last_event: dict[str, Any] | None = None  # most recent, for client animation

    # --- mutation ---------------------------------------------------------

    def apply(self, event: dict[str, Any]) -> bool:
        """Fold a bus event in. Returns True if the visible state changed."""
        etype = event.get("type")
        if not etype:
            return False
        event["image_url"] = image_ref_to_url(event.get("image"))
        self.last_event = event

        if event.get("op") == CLEAR:
            changed = self.icons.pop(etype, None) is not None
            if self.active and self.active["event"].get("type") == etype:
                self.active = None
                changed = True
            return changed

        self.icons[etype] = {"event": event, "created": event.get("ts", _now())}
        if event.get("kind") == TAKEOVER:
            ttl = int(event.get("screen_ttl") or settings.mirror_takeover_ttl)
            self.active = {"event": event, "expires": _now() + ttl * 1000}
        return True

    def dismiss(self) -> bool:
        """Touch-dismiss the takeover (the tray icon stays)."""
        if self.active is None:
            return False
        self.active = None
        return True

    def open_icon(self, etype: str) -> bool:
        """Re-show a tray icon's event as a shorter takeover."""
        entry = self.icons.get(etype)
        if not entry:
            return False
        self.active = {
            "event": entry["event"],
            "expires": _now() + settings.mirror_reopen_ttl * 1000,
        }
        return True

    def clear_icon(self, etype: str) -> bool:
        changed = self.icons.pop(etype, None) is not None
        if self.active and self.active["event"].get("type") == etype:
            self.active = None
            changed = True
        return changed

    def tick(self) -> bool:
        """Expire a stale takeover and prune icons past their TTL."""
        now = _now()
        changed = False
        if self.active and now >= self.active["expires"]:
            self.active = None
            changed = True
        cutoff = now - settings.mirror_icon_ttl_hours * 3600 * 1000
        for etype in [t for t, e in self.icons.items() if e["created"] < cutoff]:
            self.icons.pop(etype, None)
            changed = True
        return changed

    # --- serialization --------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        now = _now()
        return {
            "server_time": now,
            "active": self.active,
            "icons": [
                {
                    "type": t,
                    "event": e["event"],
                    "created": e["created"],
                    "expires": e["created"] + settings.mirror_icon_ttl_hours * 3600 * 1000,
                }
                for t, e in sorted(self.icons.items(), key=lambda kv: kv[1]["created"])
            ],
            "last_event": self.last_event,
        }

    def save(self) -> None:
        path = Path(settings.mirror_state_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"active": self.active, "icons": self.icons}))
            tmp.replace(path)
        except OSError as exc:
            log.warning("could not persist mirror state: %s", exc)

    def load(self) -> None:
        path = Path(settings.mirror_state_path)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        self.active = data.get("active")
        self.icons = data.get("icons") or {}
        # Drop anything already expired on the way back in.
        self.tick()


state = MirrorState()
