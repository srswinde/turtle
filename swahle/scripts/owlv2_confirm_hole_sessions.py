"""Pass 1: use OWLv2 to confirm/reject hole-cam presence sessions.

The classifier's raw `prob` column is unreliable (its highest scores are a
static night-IR false-positive pattern, real daytime sightings score lower —
see the naturalist-pipeline investigation). This script uses `prob > 0.70` only
as a cheap *candidate* filter (session count ~4000, not the full 14M-row
table), then checks a representative frame from each candidate session with
OWLv2 ("a tortoise" / "a turtle shell") to decide which sessions are real.

Writes confirmed sessions to a JSON file for the pass-2 shed-cam correlation
script to consume, so pass 1 (slow: one OWLv2 call per candidate session,
~4000 calls) only needs to run once.

Run inside the ml host env (needs torch + transformers + DB access):
    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python \
        swahle/scripts/owlv2_confirm_hole_sessions.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

from modules.detect.owlv2 import locate
from shared.db import SessionLocal
from shared.models import HAS_TURTLE, hole_camera

MIN_PROB = 0.70          # cheap candidate pre-filter (recall-favoring, not precision)
GAP_SECONDS = 180
CONFIRM_PROMPTS = ["a tortoise", "a turtle shell"]
CONFIRM_THRESHOLD = 0.05  # very low — real hits score 0.7+, fakes score exactly 0 hits
OUT_PATH = Path(__file__).parent / "owlv2_confirmed_sessions.json"


def load_candidate_sessions() -> pd.DataFrame:
    session = SessionLocal()
    try:
        q = (
            session.query(hole_camera.timestamp, hole_camera.prob, hole_camera.path)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .filter(hole_camera.prob > MIN_PROB)
            .order_by(hole_camera.timestamp)
        )
        df = pd.read_sql(q.statement, session.bind)
    finally:
        session.close()
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    new_session = df.timestamp.diff().gt(GAP_SECONDS * 1000)
    df["grp"] = new_session.cumsum()
    return df


def main() -> None:
    df = load_candidate_sessions()
    n_sessions = df.grp.nunique()
    print(f"candidate sessions (prob>{MIN_PROB}): {n_sessions}", file=sys.stderr)

    confirmed = []
    rejected = []
    t0 = time.time()
    for i, (grp_id, g) in enumerate(df.groupby("grp")):
        peak_row = g.loc[g.prob.idxmax()]
        try:
            with open(peak_row.path, "rb") as f:
                img_bytes = f.read()
        except OSError as exc:
            print(f"  [{i}] unreadable {peak_row.path}: {exc}", file=sys.stderr)
            continue

        hits = locate(img_bytes, prompts=CONFIRM_PROMPTS, threshold=CONFIRM_THRESHOLD)
        best_score = hits[0]["score"] if hits else 0.0
        rec = {
            "start": int(g.timestamp.min()),
            "end": int(g.timestamp.max()),
            "frames": int(len(g)),
            "peak_prob": float(peak_row.prob),
            "peak_path": peak_row.path,
            "owlv2_score": best_score,
        }
        (confirmed if best_score > 0 else rejected).append(rec)

        if i % 200 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (n_sessions - i - 1) / rate if rate > 0 else float("inf")
            print(f"  [{i}/{n_sessions}] confirmed={len(confirmed)} "
                 f"rejected={len(rejected)} eta={eta/60:.1f}min", file=sys.stderr)

    print(f"\ndone in {(time.time()-t0)/60:.1f} min", file=sys.stderr)
    print(f"confirmed: {len(confirmed)}  rejected: {len(rejected)}", file=sys.stderr)

    OUT_PATH.write_text(json.dumps({"confirmed": confirmed, "rejected_count": len(rejected)},
                                   indent=2))
    print(f"wrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
