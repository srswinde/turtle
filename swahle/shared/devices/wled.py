"""WLED LED strip via the WLED JSON API."""
from __future__ import annotations

from typing import Any

import httpx


class WledStrip:
    kind = "wled"

    def __init__(self, device_id: str, host: str, *, client: httpx.AsyncClient) -> None:
        self.id = device_id
        self._host = host.rstrip("/")
        self._client = client

    @property
    def _url(self) -> str:
        return f"http://{self._host}/json/state"

    @staticmethod
    def _color_from_state(state: dict[str, Any]) -> str:
        try:
            rgb = state.get("seg", [{}])[0].get("col", [[255, 255, 255]])[0]
            if len(rgb) >= 3:
                return f"#{int(rgb[0]):02x}{int(rgb[1]):02x}{int(rgb[2]):02x}"
        except Exception:  # noqa: BLE001
            pass
        return "#ffffff"

    async def _get(self) -> dict:
        resp = await self._client.get(self._url, timeout=4.0)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def _post(self, body: dict) -> None:
        resp = await self._client.post(self._url, json=body, timeout=4.0)
        resp.raise_for_status()

    async def state(self) -> dict:
        state = await self._get()
        return {
            "device": self.id,
            "on": bool(state.get("on", False)),
            "color": self._color_from_state(state),
            "brightness": int(state.get("bri", 0)),
        }

    async def toggle(self) -> dict:
        current = await self.state()
        return await self.set(on=not current["on"])

    async def set(self, *, on: bool | None = None, color: str | None = None,
                  brightness: int | None = None, **_ignored) -> dict:
        if color is not None:
            if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
                raise ValueError("color must be in #RRGGBB format")
            try:
                r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            except ValueError as exc:
                raise ValueError("color must be in #RRGGBB format") from exc
            await self._post({"seg": [{"col": [[r, g, b]]}]})
        if brightness is not None:
            try:
                bri = max(0, min(255, int(brightness)))
            except (TypeError, ValueError) as exc:
                raise ValueError("brightness must be an integer 0-255") from exc
            await self._post({"bri": bri})
        if on is not None:
            await self._post({"on": bool(on)})
        return await self.state()

    async def snapshot(self) -> dict:
        """Capture enough state to restore the strip later."""
        st = await self._get()
        seg0 = (st.get("seg") or [{}])[0]
        return {"on": st.get("on"), "bri": st.get("bri"),
                "seg": [{"col": seg0.get("col"), "fx": seg0.get("fx", 0),
                         "sx": seg0.get("sx"), "ix": seg0.get("ix")}]}

    async def restore(self, snap: dict) -> None:
        await self._post(snap)

    async def pulse(self, color: str, fx: int = 2, speed: int = 40) -> None:
        """Run a colored WLED effect (default Breathe) — the strip animates itself."""
        c = color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        await self._post({"on": True, "bri": 200,
                          "seg": [{"col": [[r, g, b]], "fx": fx, "sx": speed}]})

