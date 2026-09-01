"""DeepLabCut keypoint inference for the trained Cassini pose model.

Wraps ``deeplabcut.analyze_images`` (the documented single/few-image
analysis API, PyTorch-only -- matches our torch engine) rather than
hand-rolling low-level model loading, since DLC's own pre/post-processing
(cropping, augmentation, snapshot resolution) is nontrivial to replicate
correctly. Parses the CSV output (DLC's stable, documented per-frame
prediction format: header rows for scorer/bodyparts/coords, one row per
image) rather than the in-memory return value, whose exact nested shape
isn't part of the public API contract.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd

from app.settings import settings

log = logging.getLogger("swahle.detect.pose")

KEYPOINTS = ["head", "shell_front", "shell_center", "shell_back"]
DEFAULT_PCUTOFF = 0.4


def estimate(image_bytes: bytes, *, pcutoff: float = DEFAULT_PCUTOFF) -> dict[str, dict | None]:
    """Run the trained pose model on a single image.

    Returns ``{keypoint: {"x":.., "y":.., "score":..} or None}`` for each of
    ``KEYPOINTS`` (None if below pcutoff, or if no model is configured yet).
    """
    if not settings.pose_model_config:
        return {kp: None for kp in KEYPOINTS}

    import deeplabcut  # deferred: heavy import, keep ml_service module load light

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        img_path = tmp / "frame.jpg"
        img_path.write_bytes(image_bytes)

        deeplabcut.analyze_images(
            settings.pose_model_config, str(img_path),
            destfolder=str(tmp), save_as_csv=True, plotting=False,
        )

        csv_files = list(tmp.glob("*.csv"))
        if not csv_files:
            log.warning("pose analyze_images produced no CSV output for %s", img_path)
            return {kp: None for kp in KEYPOINTS}

        # DLC 3.0's PyTorch-engine output has a 4-row header (scorer,
        # individuals, bodyparts, coords) even for single-animal projects --
        # confirmed against a real trained checkpoint's output, not assumed.
        # Select by level name (not position) so an "individuals" value that
        # isn't literally "animal" doesn't silently break this.
        df = pd.read_csv(csv_files[0], header=[0, 1, 2, 3], index_col=0)
        if df.empty:
            return {kp: None for kp in KEYPOINTS}
        row = df.iloc[0]

        result: dict[str, dict | None] = {}
        for kp in KEYPOINTS:
            try:
                x = float(row.xs((kp, "x"), level=("bodyparts", "coords")).iloc[0])
                y = float(row.xs((kp, "y"), level=("bodyparts", "coords")).iloc[0])
                score = float(row.xs((kp, "likelihood"), level=("bodyparts", "coords")).iloc[0])
            except (KeyError, IndexError):
                result[kp] = None
                continue
            result[kp] = {"x": x, "y": y, "score": score} if score >= pcutoff else None
        return result
