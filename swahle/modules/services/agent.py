"""Host agent for controlling managed standalone services (systemd system units).

Runs on the host as `scott`. Reads status via `systemctl show` and tails logs via
`journalctl` (both need no privilege for scott, who is in the adm group), and
starts/stops/restarts via `sudo -n systemctl` — allowed passwordless for exactly
the whitelisted units by /etc/sudoers.d/swahle-services. Only units listed in
settings.managed_services can ever be touched.

    uvicorn modules.services.agent:app --host 0.0.0.0 --port 9300
"""
from __future__ import annotations

import subprocess

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.settings import settings

app = FastAPI(title="Swahle Services Agent")
ACTIONS = {"start", "stop", "restart"}


def _unit(name: str) -> str | None:
    cfg = settings.managed_services.get(name)
    return cfg["unit"] if cfg else None


def _status(unit: str) -> dict:
    r = subprocess.run(
        ["systemctl", "show", unit,
         "--property=LoadState,ActiveState,SubState,UnitFileState,ActiveEnterTimestamp"],
        capture_output=True, text=True, timeout=10)
    return dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/services")
def services():
    out = []
    for name, cfg in settings.managed_services.items():
        p = _status(cfg["unit"])
        out.append({
            "name": name, "unit": cfg["unit"], "description": cfg.get("description", ""),
            "load": p.get("LoadState"), "active": p.get("ActiveState"),
            "sub": p.get("SubState"), "enabled": p.get("UnitFileState"),
            "since": p.get("ActiveEnterTimestamp"),
        })
    return JSONResponse(out)


@app.post("/services/{name}/{action}")
def control(name: str, action: str):
    unit = _unit(name)
    if not unit:
        return JSONResponse({"error": f"unknown service {name}"}, status_code=404)
    if action not in ACTIONS:
        return JSONResponse({"error": f"bad action {action}"}, status_code=400)
    r = subprocess.run(["sudo", "-n", "systemctl", action, unit],
                       capture_output=True, text=True, timeout=25)
    if r.returncode != 0:
        return JSONResponse({"error": r.stderr.strip() or "systemctl failed"}, status_code=500)
    return JSONResponse({"ok": True, "active": _status(unit).get("ActiveState")})


@app.get("/services/{name}/logs")
def logs(name: str, lines: int = 120):
    unit = _unit(name)
    if not unit:
        return JSONResponse({"error": f"unknown service {name}"}, status_code=404)
    r = subprocess.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
                       capture_output=True, text=True, timeout=15)
    return JSONResponse({"log": r.stdout or r.stderr})
