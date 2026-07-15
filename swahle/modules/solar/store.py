"""Latest-reading cache + append-only JSONL history for solar telemetry."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("swahle.solar.store")


class SolarStore:
    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._latest: dict[str, dict] = {}

    def record(self, device_id: str, reading: dict) -> dict:
        entry = {**reading, "device": device_id, "ts": time.time()}
        self._latest[device_id] = entry
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:  # noqa: BLE001 - persistence is best-effort
            log.error("failed to persist solar reading for %s: %s", device_id, exc)
        return entry

    def latest(self, device_id: str) -> dict | None:
        return self._latest.get(device_id)

    def latest_all(self) -> dict[str, dict]:
        return dict(self._latest)
