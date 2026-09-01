"""Facing-direction geometry from DeepLabCut keypoints (pure function, no
DB/GPU -- consumes whatever ``modules.detect.pose.estimate()`` returns).

Only buckets into the 8 compass directions (left/right/up/down/diagonals).
Unlike the Claude exit-direction classifier (``naturalist.vision``), a
single 2D keypoint frame carries no toward/away-from-camera depth signal
(that came from watching him move across a trajectory of frames), so this
never returns those two buckets -- callers combining the two signals should
treat them as different vocabularies for a reason, not a bug.
"""
from __future__ import annotations

import math

# (direction, lower-angle-bound, upper-angle-bound] in image-space degrees,
# where 0=right, 90=down, +-180=left, -90=up (atan2 with y growing downward).
_BUCKETS = [
    ("right", -22.5, 22.5),
    ("down-right", 22.5, 67.5),
    ("down", 67.5, 112.5),
    ("down-left", 112.5, 157.5),
    ("up-left", -157.5, -112.5),
    ("up", -112.5, -67.5),
    ("up-right", -67.5, -22.5),
]


def _bucket(angle_deg: float) -> str:
    if angle_deg > 157.5 or angle_deg <= -157.5:
        return "left"
    for name, lo, hi in _BUCKETS:
        if lo < angle_deg <= hi:
            return name
    return "unclear"  # pragma: no cover - unreachable, buckets are exhaustive


def facing_direction(keypoints: dict[str, dict | None]) -> dict:
    """``keypoints``: {name: {"x":.., "y":.., "score":..} or None} for
    head/shell_front/shell_center/shell_back.

    Primary signal is shell_back -> shell_front (always visible even with
    the head retracted); `head`, when present, only raises confidence, it
    doesn't change the bucket.
    """
    back = keypoints.get("shell_back")
    front = keypoints.get("shell_front")
    if not back or not front:
        return {"direction": "unclear", "confidence": 0.0}

    dx = front["x"] - back["x"]
    dy = front["y"] - back["y"]
    if dx == 0 and dy == 0:
        return {"direction": "unclear", "confidence": 0.0}

    direction = _bucket(math.degrees(math.atan2(dy, dx)))
    confidence = min(back.get("score", 0.0), front.get("score", 0.0))

    head = keypoints.get("head")
    if head:
        confidence = min(1.0, confidence + 0.15 * head.get("score", 0.0))

    return {"direction": direction, "confidence": round(confidence, 3)}
