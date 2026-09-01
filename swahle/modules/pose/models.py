"""pose_label: per-keypoint labels collected via the custom web labeling page
(``/app/pose/label``).

Kept on its own additive declarative metadata (mirrors
``naturalist.models.PerceptionBase``) so it never touches the legacy
turtle_db schema. This table is the source of truth; the on-disk DeepLabCut
project directory built by ``scripts/pose_export_dlc.py`` is a disposable,
regeneratable artifact.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

from shared.db import SessionLocal, engine

PoseBase = declarative_base()

KEYPOINTS = ["head", "shell_front", "shell_center", "shell_back"]


class pose_label(PoseBase):
    __tablename__ = "pose_label"
    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String(512), nullable=False)     # hole-cam frame disk path (matches hole_camera.path)
    keypoint = Column(String(32), nullable=False)   # one of KEYPOINTS
    x = Column(Float)                                # image-pixel coords; NULL if not visible
    y = Column(Float)
    visible = Column(Boolean, default=True)
    labeled_at = Column(BigInteger)

    __table_args__ = (UniqueConstraint("path", "keypoint", name="uq_pose_label_path_keypoint"),)


def ensure_tables() -> None:
    PoseBase.metadata.create_all(engine, checkfirst=True)


def save_point(*, path: str, keypoint: str, x: float | None, y: float | None, visible: bool) -> None:
    if keypoint not in KEYPOINTS:
        raise ValueError(f"unknown keypoint: {keypoint}")
    session = SessionLocal()
    try:
        row = (
            session.query(pose_label)
            .filter(pose_label.path == path, pose_label.keypoint == keypoint)
            .first()
        )
        if row is None:
            row = pose_label(path=path, keypoint=keypoint)
            session.add(row)
        row.x = x
        row.y = y
        row.visible = visible
        row.labeled_at = int(dt.datetime.now().timestamp() * 1000)
        session.commit()
    finally:
        session.close()


def labeled_frame_paths() -> set[str]:
    """Frames with all 4 keypoints recorded (visible or explicitly skipped)."""
    session = SessionLocal()
    try:
        rows = session.query(pose_label.path, pose_label.keypoint).all()
    finally:
        session.close()
    by_path: dict[str, set[str]] = {}
    for path, keypoint in rows:
        by_path.setdefault(path, set()).add(keypoint)
    return {p for p, kps in by_path.items() if kps >= set(KEYPOINTS)}


def count_labeled_frames() -> int:
    return len(labeled_frame_paths())


def all_labels() -> dict[str, dict[str, dict]]:
    """Full label set for export, keyed by frame path then keypoint."""
    session = SessionLocal()
    try:
        rows = session.query(pose_label).all()
    finally:
        session.close()
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        out.setdefault(r.path, {})[r.keypoint] = {"x": r.x, "y": r.y, "visible": bool(r.visible)}
    return out
