"""Pass 2: does a confirmed hole-cam departure predict a real tortoise sighting
on the shed cam shortly after? Uses OWLv2 directly on shed-cam frames — not the
COCO detector (already shown uncorrelated) — compared against a random-time
baseline of the same size.

Consumes scripts/owlv2_confirmed_sessions.json (pass 1 output). To keep this
tractable, samples N departures (not all ~2500) and K frames per time window
(not every ~7s frame) rather than brute-forcing the full archive.

Run inside the ml host env:
    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python \
        swahle/scripts/owlv2_shed_correlation.py
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from modules.detect.owlv2 import locate
from shared.db import SessionLocal
from shared.models import shed_camera

CONFIRMED_PATH = Path(__file__).parent / "owlv2_confirmed_sessions.json"
SCORE_CUTOFF = 0.3          # pass-1 score cutoff for "real" departure
N_DEPARTURES = 250          # sample size (of ~2500 confirmed) — keeps runtime ~20min
WINDOW_SECONDS = 600         # how long after departure to look on the shed cam
FRAMES_PER_WINDOW = 15       # evenly-sampled shed frames checked per window
DETECT_PROMPTS = ["a tortoise", "a turtle shell"]
HIT_THRESHOLD = 0.2         # score above which we call it a real shed-cam sighting
SEED = 20260710


def load_departures() -> list[dict]:
    data = json.loads(CONFIRMED_PATH.read_text())
    conf = [c for c in data["confirmed"] if c["owlv2_score"] > SCORE_CUTOFF]
    print(f"confirmed departures score>{SCORE_CUTOFF}: {len(conf)}", file=sys.stderr)
    rng = random.Random(SEED)
    if len(conf) > N_DEPARTURES:
        conf = rng.sample(conf, N_DEPARTURES)
    return conf


def shed_frames_in_window(start_ms: int, end_ms: int, k: int) -> list[str]:
    session = SessionLocal()
    try:
        rows = (
            session.query(shed_camera.path)
            .filter(shed_camera.timestamp >= start_ms)
            .filter(shed_camera.timestamp < end_ms)
            .order_by(shed_camera.timestamp)
            .all()
        )
    finally:
        session.close()
    paths = [r.path for r in rows if r.path]
    if len(paths) <= k:
        return paths
    idx = np.linspace(0, len(paths) - 1, k).round().astype(int)
    return [paths[i] for i in sorted(set(idx))]


def score_window(paths: list[str]) -> float:
    best = 0.0
    for p in paths:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue
        hits = locate(data, prompts=DETECT_PROMPTS, threshold=0.05)
        if hits:
            best = max(best, hits[0]["score"])
    return best


def shed_time_bounds() -> tuple[int, int]:
    from sqlalchemy import func

    session = SessionLocal()
    try:
        lo, hi = session.query(func.min(shed_camera.timestamp),
                               func.max(shed_camera.timestamp)).one()
    finally:
        session.close()
    return int(lo), int(hi)


def main() -> None:
    departures = load_departures()
    shed_lo, shed_hi = shed_time_bounds()
    rng = random.Random(SEED + 1)

    results = {"departure_windows": [], "baseline_windows": []}
    t0 = time.time()

    print(f"scoring {len(departures)} departure windows "
         f"({WINDOW_SECONDS}s, {FRAMES_PER_WINDOW} frames each)...", file=sys.stderr)
    for i, dep in enumerate(departures):
        end = dep["end"]
        paths = shed_frames_in_window(end, end + WINDOW_SECONDS * 1000, FRAMES_PER_WINDOW)
        score = score_window(paths)
        results["departure_windows"].append({
            "departure_end": end, "n_frames": len(paths), "best_score": score,
        })
        if i % 25 == 0:
            elapsed = time.time() - t0
            print(f"  [{i}/{len(departures)}] elapsed={elapsed/60:.1f}min", file=sys.stderr)

    print(f"scoring {len(departures)} random baseline windows...", file=sys.stderr)
    for i, dep in enumerate(departures):
        # random control time, clear of the shed data range edges
        rand_ms = rng.randint(shed_lo, shed_hi - WINDOW_SECONDS * 1000)
        paths = shed_frames_in_window(rand_ms, rand_ms + WINDOW_SECONDS * 1000,
                                      FRAMES_PER_WINDOW)
        score = score_window(paths)
        results["baseline_windows"].append({
            "start": rand_ms, "n_frames": len(paths), "best_score": score,
        })
        if i % 25 == 0:
            elapsed = time.time() - t0
            print(f"  [{i}/{len(departures)}] elapsed={elapsed/60:.1f}min", file=sys.stderr)

    print(f"\ndone in {(time.time()-t0)/60:.1f} min", file=sys.stderr)

    dep_scores = pd.Series([r["best_score"] for r in results["departure_windows"]])
    base_scores = pd.Series([r["best_score"] for r in results["baseline_windows"]])
    dep_hit_rate = (dep_scores > HIT_THRESHOLD).mean()
    base_hit_rate = (base_scores > HIT_THRESHOLD).mean()
    print(f"departure-window hit rate (score>{HIT_THRESHOLD}): {dep_hit_rate:.1%} "
         f"({(dep_scores>HIT_THRESHOLD).sum()}/{len(dep_scores)})", file=sys.stderr)
    print(f"baseline-window hit rate:  {base_hit_rate:.1%} "
         f"({(base_scores>HIT_THRESHOLD).sum()}/{len(base_scores)})", file=sys.stderr)
    print(f"lift: {dep_hit_rate/base_hit_rate if base_hit_rate>0 else float('inf'):.2f}x",
         file=sys.stderr)

    out_path = Path(__file__).parent / "owlv2_shed_correlation_results.json"
    out_path.write_text(json.dumps({
        "params": {"score_cutoff": SCORE_CUTOFF, "n_departures": len(departures),
                   "window_seconds": WINDOW_SECONDS, "frames_per_window": FRAMES_PER_WINDOW,
                   "hit_threshold": HIT_THRESHOLD},
        "departure_windows": results["departure_windows"],
        "baseline_windows": results["baseline_windows"],
    }, indent=2))
    print(f"wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
