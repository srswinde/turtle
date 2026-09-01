"""Where does Cassini actually show up on the shed cam? Mines recent shed-cam
history directly with OWLv2 (independent of hole-cam departures) to find real
sightings, their pixel locations, and how much the camera's own framing varies
across them — informs whether Phase 2 needs new PTZ waypoints or can just
detect against whatever position the camera (which already seems to reposition
itself periodically — "PTZ booting up" overlays seen in the wild) happens to be
pointed at.

Restricted to the last N_DAYS: spot-checking frames from mid-2024 through 2026
showed the camera's framing/zoom has changed substantially over its 2-year
history, so a full-archive spatial heatmap would mix incompatible framings.

Run inside the ml host env:
    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python \
        swahle/scripts/owlv2_shed_spot_mining.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

from modules.detect.owlv2 import locate
from shared.db import SessionLocal
from shared.models import shed_camera

N_DAYS = 90
SAMPLE_INTERVAL_SECONDS = 300   # one frame every 5 minutes
DAY_START_HOUR, DAY_END_HOUR = 8, 21   # local-ish window (data stored as UTC; see note)
DETECT_PROMPTS = ["a tortoise", "a turtle shell"]
HIT_THRESHOLD = 0.2
OUT_PATH = Path(__file__).parent / "owlv2_shed_spots.json"


def sampled_timestamps() -> list[tuple[int, str]]:
    session = SessionLocal()
    try:
        end_ms = int(dt.datetime.now().timestamp() * 1000)
        start_ms = end_ms - N_DAYS * 86400 * 1000
        rows = (
            session.query(shed_camera.timestamp, shed_camera.path)
            .filter(shed_camera.timestamp >= start_ms)
            .filter(shed_camera.timestamp < end_ms)
            .order_by(shed_camera.timestamp)
            .all()
        )
    finally:
        session.close()

    out = []
    last_kept = -1
    for ts, path in rows:
        if not path:
            continue
        local_hour = (dt.datetime.utcfromtimestamp(ts / 1000) - dt.timedelta(hours=7)).hour
        if not (DAY_START_HOUR <= local_hour < DAY_END_HOUR):
            continue
        if last_kept < 0 or ts - last_kept >= SAMPLE_INTERVAL_SECONDS * 1000:
            out.append((ts, path))
            last_kept = ts
    return out


def main() -> None:
    samples = sampled_timestamps()
    print(f"sampling {len(samples)} shed-cam frames over the last {N_DAYS} days "
         f"({DAY_START_HOUR}-{DAY_END_HOUR}h local, every {SAMPLE_INTERVAL_SECONDS}s)",
         file=sys.stderr)

    hits = []
    t0 = time.time()
    for i, (ts, path) in enumerate(samples):
        try:
            with open(path, "rb") as f:
                img_bytes = f.read()
        except OSError:
            continue
        found = locate(img_bytes, prompts=DETECT_PROMPTS, threshold=0.05)
        best = found[0] if found else None
        if best and best["score"] > HIT_THRESHOLD:
            x1, y1, x2, y2 = best["box"]
            hits.append({
                "timestamp": ts, "path": path, "score": best["score"],
                "box": best["box"], "center": [(x1 + x2) / 2, (y1 + y2) / 2],
            })
        if i % 500 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(samples) - i - 1) / rate if rate > 0 else float("inf")
            print(f"  [{i}/{len(samples)}] hits={len(hits)} eta={eta/60:.1f}min",
                 file=sys.stderr)

    print(f"\ndone in {(time.time()-t0)/60:.1f} min — {len(hits)} hits "
         f"of {len(samples)} sampled frames", file=sys.stderr)
    OUT_PATH.write_text(json.dumps({
        "params": {"n_days": N_DAYS, "interval_seconds": SAMPLE_INTERVAL_SECONDS,
                   "hit_threshold": HIT_THRESHOLD, "n_sampled": len(samples)},
        "hits": hits,
    }, indent=2))
    print(f"wrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
