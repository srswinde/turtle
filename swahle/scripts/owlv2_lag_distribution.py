"""Pass 3: for every departure-window hit from pass 2, find the exact
best-scoring shed-cam frame and its lag from the hole-cam departure.

Also builds a small curated "example detections" manifest (path + score + lag)
for the /app/naturalist/owlv2 findings page.

Run inside the ml host env:
    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python \
        swahle/scripts/owlv2_lag_distribution.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from modules.detect.owlv2 import locate
from shared.db import SessionLocal
from shared.models import shed_camera

RESULTS_PATH = Path(__file__).parent / "owlv2_shed_correlation_results.json"
OUT_PATH = Path(__file__).parent / "owlv2_lag_distribution.json"
DETECT_PROMPTS = ["a tortoise", "a turtle shell"]
HIT_THRESHOLD = 0.2
FRAMES_PER_WINDOW = 15
WINDOW_SECONDS = 600


def frames_in_window(start_ms: int, end_ms: int, k: int) -> list[tuple[int, str]]:
    session = SessionLocal()
    try:
        rows = (
            session.query(shed_camera.timestamp, shed_camera.path)
            .filter(shed_camera.timestamp >= start_ms)
            .filter(shed_camera.timestamp < end_ms)
            .order_by(shed_camera.timestamp)
            .all()
        )
    finally:
        session.close()
    rows = [(r.timestamp, r.path) for r in rows if r.path]
    if len(rows) <= k:
        return rows
    idx = np.linspace(0, len(rows) - 1, k).round().astype(int)
    return [rows[i] for i in sorted(set(idx))]


def main() -> None:
    data = json.loads(RESULTS_PATH.read_text())
    hits = [r for r in data["departure_windows"] if r["best_score"] > HIT_THRESHOLD]
    print(f"{len(hits)} departure-window hits to re-localize", file=sys.stderr)

    out = []
    t0 = time.time()
    for i, h in enumerate(hits):
        dep_end = h["departure_end"]
        rows = frames_in_window(dep_end, dep_end + WINDOW_SECONDS * 1000, FRAMES_PER_WINDOW)
        best = None  # (score, ts, path, box)
        for ts, path in rows:
            try:
                with open(path, "rb") as f:
                    img_bytes = f.read()
            except OSError:
                continue
            found = locate(img_bytes, prompts=DETECT_PROMPTS, threshold=0.05)
            score = found[0]["score"] if found else 0.0
            box = found[0]["box"] if found else None
            if best is None or score > best[0]:
                best = (score, ts, path, box)
        if best is None:
            continue
        score, ts, path, box = best
        out.append({
            "departure_end": dep_end,
            "shed_timestamp": ts,
            "lag_seconds": round((ts - dep_end) / 1000, 1),
            "score": score,
            "path": path,
            "box": box,
        })
        print(f"  [{i+1}/{len(hits)}] lag={out[-1]['lag_seconds']:.0f}s score={score:.3f}",
             file=sys.stderr)

    lags = [r["lag_seconds"] for r in out]
    if lags:
        arr = np.array(lags)
        print(f"\nlag stats (n={len(arr)}): median={np.median(arr):.0f}s "
             f"mean={arr.mean():.0f}s p25={np.percentile(arr,25):.0f}s "
             f"p75={np.percentile(arr,75):.0f}s max={arr.max():.0f}s",
             file=sys.stderr)

    print(f"done in {(time.time()-t0)/60:.1f} min", file=sys.stderr)
    OUT_PATH.write_text(json.dumps({"hits": out}, indent=2))
    print(f"wrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
