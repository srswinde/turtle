"""Minimal HTTP service for the isolated `ml` container.

The web app is torch-free, so it delegates the actual training launch here.
Everything else (status, logs, checkpoint listing, activate/rollback) is
file-based on the shared model_root volume and read directly by the web app,
so this service only needs to trigger a run.

    uvicorn modules.detect.ml_service:app --host 0.0.0.0 --port 9200
"""
from __future__ import annotations

import base64

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import training

app = FastAPI(title="Swahle ML")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.post("/detect/locate")
async def detect_locate(request: Request):
    """Open-vocabulary object localization (OWLv2) — e.g. "find the tortoise" on a
    frame from any camera. Body: {"image": "<base64 jpeg>", "prompts": [...],
    "threshold": 0.15}. ``prompts``/``threshold`` are optional (see owlv2.py defaults).
    """
    from . import owlv2  # deferred: torch import, keep module load light

    body = await request.json()
    image_b64 = body.get("image")
    if not image_b64:
        return JSONResponse({"error": "image (base64) is required"}, status_code=400)
    try:
        image_bytes = base64.b64decode(image_b64)
    except (ValueError, TypeError):
        return JSONResponse({"error": "image is not valid base64"}, status_code=400)

    kwargs = {}
    if body.get("prompts"):
        kwargs["prompts"] = body["prompts"]
    if body.get("threshold") is not None:
        kwargs["threshold"] = float(body["threshold"])

    try:
        hits = owlv2.locate(image_bytes, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"detection failed: {exc}"}, status_code=500)
    return JSONResponse({"hits": hits})


@app.post("/pose/estimate")
async def pose_estimate(request: Request):
    """DeepLabCut keypoint estimation for the Cassini facing-direction pose
    model. Body: {"image": "<base64 jpeg>"}. Returns
    {keypoints: {name: {x,y,score} or null}, facing: {direction, confidence}}.
    """
    from . import pose, pose_geometry  # deferred: heavy import, keep module load light

    body = await request.json()
    image_b64 = body.get("image")
    if not image_b64:
        return JSONResponse({"error": "image (base64) is required"}, status_code=400)
    try:
        image_bytes = base64.b64decode(image_b64)
    except (ValueError, TypeError):
        return JSONResponse({"error": "image is not valid base64"}, status_code=400)

    try:
        keypoints = pose.estimate(image_bytes)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"pose estimation failed: {exc}"}, status_code=500)
    facing = pose_geometry.facing_direction(keypoints)
    return JSONResponse({"keypoints": keypoints, "facing": facing})


@app.post("/train")
async def train(request: Request):
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass
    try:
        st = training._start_local(
            since=body.get("since"),
            num_finetunes=body.get("num_finetunes"),
            num_samples=body.get("num_samples"),
            output_dir=body.get("output_dir"),
        )
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse(st)


@app.get("/status")
async def status():
    return JSONResponse(training.status())
