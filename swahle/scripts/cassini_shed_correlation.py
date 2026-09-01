"""One-off analysis: does a hole-cam departure predict a shed-cam detection shortly after?

One-off / rerunnable analysis script, not part of the app. Run inside the web
container (needs DB access via shared.db):
    docker cp scripts/cassini_shed_correlation.py swahle-web:/app/swahle/scripts/
    docker exec swahle-web python3 scripts/cassini_shed_correlation.py
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from shared.db import SessionLocal
from shared.models import HAS_TURTLE, hole_camera, shed_camera_resnet_detection

MIN_PROB = 0.70
GAP_SECONDS = 180
# Classes Cassini is plausibly misclassified as on the shed cam too (animals + the
# hole-cam's dominant misfires). Excludes obvious static-object noise (chair, potted
# plant, umbrella-as-patio-umbrella, tv, etc.) that fire millions of times on fixed
# background objects.
CANDIDATE_CLASSES = ["bird", "cat", "dog", "cow", "horse", "sheep", "bear",
                     "elephant", "zebra", "giraffe", "teddy bear"]
WINDOWS_SECONDS = [30, 60, 120, 300, 600, 900]


def load_hole_sessions() -> pd.DataFrame:
    session = SessionLocal()
    try:
        q = (
            session.query(hole_camera.timestamp, hole_camera.prob)
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NO)
            .order_by(hole_camera.timestamp)
        )
        df = pd.read_sql(q.statement, session.bind)
    finally:
        session.close()
    df = df[df.prob > MIN_PROB].copy()
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    new_session = df.timestamp.diff().gt(GAP_SECONDS * 1000)
    df["grp"] = new_session.cumsum()
    sessions = df.groupby("grp").agg(start=("timestamp", "min"), end=("timestamp", "max"),
                                     frames=("timestamp", "size"), peak_prob=("prob", "max"))
    return sessions.reset_index(drop=True)


def load_shed_detections() -> pd.DataFrame:
    session = SessionLocal()
    try:
        q = (
            session.query(shed_camera_resnet_detection.timestamp,
                          shed_camera_resnet_detection.class_name)
            .filter(shed_camera_resnet_detection.class_name.in_(CANDIDATE_CLASSES))
            .order_by(shed_camera_resnet_detection.timestamp)
        )
        df = pd.read_sql(q.statement, session.bind)
    finally:
        session.close()
    return df


def main() -> None:
    sessions = load_hole_sessions()
    shed = load_shed_detections()
    shed_ts = shed.timestamp.to_numpy()

    print(f"hole-cam presence sessions (prob>{MIN_PROB}): {len(sessions)}")
    print(f"shed-cam candidate-class detections: {len(shed)} "
         f"({shed.class_name.nunique()} classes)")
    print("class breakdown:", shed.class_name.value_counts().to_dict())

    span_ms = shed_ts.max() - shed_ts.min()
    span_s = span_ms / 1000
    baseline_rate_per_s = len(shed) / span_s  # candidate detections per second, overall

    print(f"\noverall candidate-class detection rate: {baseline_rate_per_s * 60:.4f} / min "
         f"(baseline for comparison)")

    departures = sessions.end.to_numpy()
    print(f"\n{'window(s)':>10} {'departures_w/hit':>18} {'hit_rate':>10} "
         f"{'expected_by_chance':>20} {'lift':>8}")
    for w in WINDOWS_SECONDS:
        w_ms = w * 1000
        # for each departure, does a shed detection fall in [dep, dep+w]?
        idx_lo = np.searchsorted(shed_ts, departures, side="left")
        idx_hi = np.searchsorted(shed_ts, departures + w_ms, side="right")
        hits = (idx_hi > idx_lo).sum()
        hit_rate = hits / len(departures)
        expected = 1 - np.exp(-baseline_rate_per_s * w)  # poisson: P(>=1 event in window)
        lift = hit_rate / expected if expected > 0 else float("nan")
        print(f"{w:>10} {hits:>10}/{len(departures):<7} {hit_rate:>9.1%} "
             f"{expected:>19.1%} {lift:>7.2f}x")

    # timing distribution of the hits at the widest window, for the ones that hit
    w_ms = WINDOWS_SECONDS[-1] * 1000
    idx_lo = np.searchsorted(shed_ts, departures, side="left")
    idx_hi = np.searchsorted(shed_ts, departures + w_ms, side="right")
    lags = []
    for dep, lo, hi in zip(departures, idx_lo, idx_hi):
        if hi > lo:
            lags.append((shed_ts[lo] - dep) / 1000)  # seconds
    if lags:
        lags = np.array(lags)
        print(f"\nlag to first shed hit within {WINDOWS_SECONDS[-1]}s window "
             f"(n={len(lags)}):")
        print(f"  median={np.median(lags):.0f}s  p25={np.percentile(lags,25):.0f}s  "
             f"p75={np.percentile(lags,75):.0f}s")

    # sanity: departure-hour histogram vs user's expected 11am-2pm appearance window
    starts = pd.to_datetime(sessions.start, unit="ms")
    print("\nhole-cam session START hour histogram (local server time, unadjusted tz):")
    print(starts.dt.hour.value_counts().sort_index().to_string())


if __name__ == "__main__":
    main()
