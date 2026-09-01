"""Pose-labeling routes (private): collect keypoint labels for DeepLabCut.

``/app/pose/label`` serves one frame at a time from the sampled queue
(``labeling.sample_frames``); the labeler clicks each of the 4 keypoints (or
marks "not visible") and posts them back via ``POST /api/pose/label``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.deps import require_user
from app.templating import templates

from . import labeling
from .models import KEYPOINTS, count_labeled_frames, labeled_frame_paths, save_point

router = APIRouter(prefix="/app", tags=["pose"], dependencies=[Depends(require_user)])


@router.get("/pose/label", response_class=HTMLResponse)
async def pose_label_page(request: Request):
    return templates.TemplateResponse(request, "pose-label.html")


@router.get("/api/pose/next-frame")
async def next_frame():
    queue = labeling.sample_frames()
    done = labeled_frame_paths()
    remaining = [p for p in queue if p not in done]
    if not remaining:
        return JSONResponse({"path": None, "labeled": len(done), "target": len(queue)})
    path = remaining[0]
    return JSONResponse({
        "path": path,
        "image_url": labeling.frame_url(path),
        "keypoints": KEYPOINTS,
        "labeled": len(done),
        "target": len(queue),
    })


@router.post("/api/pose/label")
async def label_frame(request: Request):
    body = await request.json()
    path = body.get("path")
    points = body.get("points")  # {keypoint: {x, y, visible}}
    if not path or not points:
        return JSONResponse({"error": "path and points required"}, status_code=400)
    for kp in KEYPOINTS:
        p = points.get(kp)
        if not p:
            return JSONResponse({"error": f"missing keypoint: {kp}"}, status_code=400)
        save_point(path=path, keypoint=kp, x=p.get("x"), y=p.get("y"),
                  visible=bool(p.get("visible", True)))
    return JSONResponse({"ok": True})


@router.get("/api/pose/progress")
async def progress():
    queue = labeling.sample_frames()
    return JSONResponse({"labeled": count_labeled_frames(), "target": len(queue)})
