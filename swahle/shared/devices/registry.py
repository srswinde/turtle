"""Device registry, built from settings.

Exposes devices by id to the dashboard module so the UI never touches
device-specific transport. Shares one httpx client across all devices.
"""
from __future__ import annotations

import httpx

from app.settings import settings

from .base import Device
from .shelly import ShellyRelay
from .wled import WledStrip


class DeviceRegistry:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient()
        self._devices: dict[str, Device] = {}

        for device_id, cfg in settings.shelly_devices.items():
            self._devices[device_id] = ShellyRelay(
                device_id,
                cfg["endpoint"],
                switch_id=int(cfg.get("switch_id", 0)),
                client=self._client,
            )
        for device_id, host in settings.wled_devices.items():
            self._devices[device_id] = WledStrip(device_id, host, client=self._client)

    def get(self, device_id: str) -> Device:
        if device_id not in self._devices:
            raise KeyError(f"Unknown device: {device_id}")
        return self._devices[device_id]

    def all(self) -> list[Device]:
        return list(self._devices.values())

    async def aclose(self) -> None:
        await self._client.aclose()
