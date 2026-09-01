"""Detect module routes (private): retrain UI + model management.

Mounted at ``/app`` behind ``require_user``. Torch-free — training runs in the
isolated subprocess (modules.detect.train); this only starts it and reports
status/logs, and manages the active-checkpoint pointer.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.deps import require_user
from app.templating import templates

from . import registry, training

log = logging.getLogger("swahle.detect")

router = APIRouter(prefix="/app", tags=["private"], dependencies=[Depends(require_user)])


@router.get("/detect", response_class=HTMLResponse)
async def retrain_page(request: Request):
    return templates.TemplateResponse(request, "retrain.html")


@router.post("/api/detect/train")
async def start_training(request: Request):
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass
    try:
        st = training.start(
            since=body.get("since"),
            num_finetunes=body.get("num_finetunes"),
            num_samples=body.get("num_samples"),
            output_dir=body.get("output_dir"),
        )
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse(st)


@router.get("/api/detect/status")
async def training_status():
    return JSONResponse(training.status())


@router.get("/api/detect/logs")
async def training_logs(lines: int = 200):
    return JSONResponse({"log": training.logs(lines)})


@router.get("/api/detect/models")
async def list_models():
    return JSONResponse({
        "models": registry.list_checkpoints(),
        "active": registry.active_model_path(),
    })


@router.post("/api/detect/activate")
async def activate_model(request: Request):
    body = await request.json()
    path = body.get("path")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)
    try:
        active = registry.set_active(path)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"active": active})


@router.post("/api/detect/rollback")
async def rollback_model():
    try:
        active = registry.rollback()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"active": active})
