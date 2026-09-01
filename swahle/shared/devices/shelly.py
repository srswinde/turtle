"""Shelly Plus relay (lights, water pump) via the Shelly RPC API."""
from __future__ import annotations

import httpx


class ShellyRelay:
    kind = "shelly"

    def __init__(self, device_id: str, endpoint: str, *, switch_id: int = 0,
                 client: httpx.AsyncClient) -> None:
        self.id = device_id
        self._endpoint = endpoint.rstrip("/")
        self._switch_id = switch_id
        self._client = client
        self._is_on = False  # last-known, used as a fallback

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload = {"id": 0, "method": method, "params": params or {"id": self._switch_id}}
        resp = await self._client.post(self._endpoint, json=payload, timeout=4.0)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def state(self) -> dict:
        resp = await self._rpc("Switch.GetStatus")
        self._is_on = bool(resp.get("result", {}).get("output", self._is_on))
        return {"device": self.id, "on": self._is_on}

    async def toggle(self) -> dict:
        resp = await self._rpc("Switch.Toggle")
        was_on = bool(resp.get("result", {}).get("was_on", not self._is_on))
        self._is_on = not was_on
        return {"device": self.id, "on": self._is_on}

    async def set(self, *, on: bool, **_ignored) -> dict:
        await self._rpc("Switch.Set", {"id": self._switch_id, "on": bool(on)})
        self._is_on = bool(on)
        return {"device": self.id, "on": self._is_on}

    def cached_state(self) -> dict:
        return {"device": self.id, "on": self._is_on}

    # --- Shelly Gen2 Webhook management (Webhook.List/Create/Delete) --------
    async def list_webhooks(self) -> list[dict]:
        r = await self._rpc("Webhook.List", {})
        return (r.get("result") or {}).get("hooks", [])

    async def create_webhook(self, event: str, urls: list[str], name: str | None = None,
                             enable: bool = True) -> dict:
        params = {"cid": self._switch_id, "enable": enable, "event": event, "urls": urls}
        if name:
            params["name"] = name
        r = await self._rpc("Webhook.Create", params)
        return r.get("result") or {}

    async def delete_webhook(self, hook_id: int) -> None:
        await self._rpc("Webhook.Delete", {"id": hook_id})

