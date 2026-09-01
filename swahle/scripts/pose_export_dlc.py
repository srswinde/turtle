"""Export ``pose_label`` DB rows into a DeepLabCut (PyTorch engine) project.

Standalone, run manually via the anaconda host python (same pattern as
``scripts/owlv2_*.py``) -- NOT triggered automatically by the web app.
Source of truth is the ``pose_label`` DB table (filled in via the
``/app/pose/label`` web UI); the project directory this writes is a
disposable, regeneratable artifact -- safe to delete and re-run.

    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python scripts/pose_export_dlc.py \\
        --output-dir /mnt/nfs/models/pose --project-name cassini-pose

DeepLabCut's ``create_new_project`` requires at least one real, openable
video to bootstrap a project (it probes it with OpenCV) even for a
photo-only dataset -- we synthesize a trivial 2-frame placeholder video from
one of the labeled frames purely to satisfy that. The real labeled images
are then placed directly in the resulting ``labeled-data/<name>/`` folder,
which is all ``create_training_dataset``/``train_network`` actually read;
the placeholder video itself is never analyzed.

CollectedData row/column format confirmed against the installed DLC 3.0
source (``deeplabcut/utils/conversioncode.py::convertcsv2h5``): columns are
a 3-level MultiIndex (scorer, bodyparts, coords), rows a 3-level MultiIndex
of path parts (("labeled-data", <video-stem>, <image-filename>)), saved to
``CollectedData_<scorer>.h5`` under key ``"df_with_missing"``.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from modules.pose.models import KEYPOINTS, all_labels


def _make_placeholder_video(reference_image: Path, out_path: Path) -> None:
    img = cv2.imread(str(reference_image))
    if img is None:
        raise RuntimeError(f"could not read reference image: {reference_image}")
    h, w = img.shape[:2]
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), 1.0, (w, h))
    writer.write(img)
    writer.write(img)
    writer.release()


def export(output_dir: str, project_name: str = "cassini-pose", experimenter: str = "scott") -> str:
    labels = all_labels()  # {path: {keypoint: {x, y, visible}}}
    complete = {p: kp for p, kp in labels.items() if set(kp) >= set(KEYPOINTS)}
    if not complete:
        raise RuntimeError(
            "no fully-labeled frames in pose_label -- label some via /app/pose/label first")

    import deeplabcut
    from deeplabcut.utils import auxiliaryfunctions

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scratch = output_dir / "_placeholder_video"
    scratch.mkdir(exist_ok=True)
    placeholder = scratch / "cassini-frames.mp4"
    _make_placeholder_video(Path(next(iter(complete))), placeholder)

    config_path = deeplabcut.create_new_project(
        project_name, experimenter, videos=[str(placeholder)],
        working_directory=str(output_dir), copy_videos=False,
    )
    if config_path == "nothingcreated":
        raise RuntimeError(
            "DeepLabCut could not open the placeholder video -- check OpenCV/ffmpeg codec support")

    cfg = auxiliaryfunctions.read_config(config_path)
    cfg["bodyparts"] = KEYPOINTS
    cfg["skeleton"] = [["shell_back", "shell_center"], ["shell_center", "shell_front"],
                        ["shell_front", "head"]]
    auxiliaryfunctions.write_config(config_path, cfg)
    scorer = cfg["scorer"]

    project_path = Path(config_path).parent
    data_folder = project_path / "labeled-data" / placeholder.stem

    columns = pd.MultiIndex.from_product([[scorer], KEYPOINTS, ["x", "y"]],
                                         names=["scorer", "bodyparts", "coords"])
    row_index = []
    row_values = []
    for i, (frame_path, points) in enumerate(sorted(complete.items())):
        src = Path(frame_path)
        dest_name = f"img{i:04d}{src.suffix or '.jpg'}"
        shutil.copy(src, data_folder / dest_name)

        values = []
        for kp in KEYPOINTS:
            p = points[kp]
            values.append(p["x"] if p["visible"] else np.nan)
            values.append(p["y"] if p["visible"] else np.nan)
        row_values.append(values)
        row_index.append(("labeled-data", placeholder.stem, dest_name))

    df = pd.DataFrame(row_values, index=pd.MultiIndex.from_tuples(row_index), columns=columns)

    collected = data_folder / f"CollectedData_{scorer}"
    df.to_hdf(str(collected) + ".h5", key="df_with_missing", mode="w")
    df.to_csv(str(collected) + ".csv")

    print(f"Exported {len(df)} labeled frames to {data_folder}")
    print(f"Project config: {config_path}")
    return config_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Export pose_label DB rows to a DeepLabCut project.")
    ap.add_argument("--output-dir", default="/mnt/nfs/models/pose")
    ap.add_argument("--project-name", default="cassini-pose")
    ap.add_argument("--experimenter", default="scott")
    args = ap.parse_args()
    export(args.output_dir, args.project_name, args.experimenter)


if __name__ == "__main__":
    main()
