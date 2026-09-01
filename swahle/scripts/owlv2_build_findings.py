"""Pass 4: assemble the findings.json + annotated example images consumed by
the /app/naturalist/owlv2 web page.

Reads the outputs of the previous three passes (confirm/correlate/lag) and
writes a compact summary (histograms, not raw per-frame dumps) plus a small
gallery of OWLv2-boxed example detections — including one flagged likely-false-
positive, for an honest picture of the detector's precision.

Writes into /mnt/nfs/naturalist/owlv2/ so the (separate) web container can
read it directly — no rebuild needed to update the findings page.

Run inside the ml host env:
    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python \
        swahle/scripts/owlv2_build_findings.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPTS_DIR = Path(__file__).parent
CONFIRMED_PATH = SCRIPTS_DIR / "owlv2_confirmed_sessions.json"
CORRELATION_PATH = SCRIPTS_DIR / "owlv2_shed_correlation_results.json"
LAG_PATH = SCRIPTS_DIR / "owlv2_lag_distribution.json"

OUT_DIR = Path("/mnt/nfs/naturalist/owlv2")
EXAMPLES_DIR = OUT_DIR / "examples"
FINDINGS_PATH = OUT_DIR / "findings.json"

HOLE_CAM_DIR = Path("/mnt/nfs/hole-cam")
SHED_CAM_DIR = Path("/mnt/nfs/shed-cam")

# Hand-curated from visual inspection during the investigation (see conversation):
# real hits confirmed by eye, plus one flagged false positive for honesty.
CURATED_DEPARTURE_ENDS = {
    1780162393304: {"verified": True, "note": "Clear daytime sighting, walking near the patio."},
    1763399751378: {"verified": True, "note": "Clear sighting on the artificial turf."},
    1781736056534: {"verified": False,
                    "note": "Likely false positive — a tarp/shadow shape, no tortoise visible."},
}


def histogram(values: list[float], bins: list[float]) -> list[dict]:
    counts = [0] * (len(bins) - 1)
    for v in values:
        for i in range(len(bins) - 1):
            lo, hi = bins[i], bins[i + 1]
            if (lo <= v < hi) or (i == len(bins) - 2 and v == hi):
                counts[i] += 1
                break
    return [{"lo": bins[i], "hi": bins[i + 1], "count": counts[i]} for i in range(len(counts))]


def rel_url(path: str, root: Path, prefix: str) -> str | None:
    try:
        rel = Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return f"{prefix}/{rel.as_posix()}"


def draw_example(src_path: str, box: list[float] | None, out_name: str,
                 label: str, max_px: int = 900) -> None:
    img = Image.open(src_path).convert("RGB")
    if box:
        draw = ImageDraw.Draw(img)
        x1, y1, x2, y2 = box
        draw.rectangle([x1, y1, x2, y2], outline=(255, 60, 40), width=5)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        except OSError:
            font = ImageFont.load_default()
        text_y = max(0, y1 - 30)
        draw.rectangle([x1, text_y, x1 + 9 * len(label) + 10, text_y + 28], fill=(255, 60, 40))
        draw.text((x1 + 5, text_y + 3), label, fill=(255, 255, 255), font=font)
    img.thumbnail((max_px, max_px))
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    img.save(EXAMPLES_DIR / out_name, format="JPEG", quality=85)


def main() -> None:
    confirmed_data = json.loads(CONFIRMED_PATH.read_text())
    correlation_data = json.loads(CORRELATION_PATH.read_text())
    lag_data = json.loads(LAG_PATH.read_text())

    confirmed = confirmed_data["confirmed"]
    n_candidates = len(confirmed) + confirmed_data["rejected_count"]
    confirmed_scores = [c["owlv2_score"] for c in confirmed]

    dep_scores = [r["best_score"] for r in correlation_data["departure_windows"]]
    base_scores = [r["best_score"] for r in correlation_data["baseline_windows"]]
    params = correlation_data["params"]
    dep_hit_rate = sum(s > params["hit_threshold"] for s in dep_scores) / len(dep_scores)
    base_hit_rate = sum(s > params["hit_threshold"] for s in base_scores) / len(base_scores)

    lags = [h["lag_seconds"] for h in lag_data["hits"]]

    # by-index lookup so curated examples can find their departure's own record
    hits_by_dep_end = {h["departure_end"]: h for h in lag_data["hits"]}
    confirmed_by_end = {c["end"]: c for c in confirmed}

    examples = []
    for dep_end, meta in CURATED_DEPARTURE_ENDS.items():
        hit = hits_by_dep_end.get(dep_end)
        if not hit:
            print(f"WARN: no lag-distribution hit for departure_end={dep_end}", file=sys.stderr)
            continue
        out_name = f"shed_{dep_end}.jpg"
        label = "tortoise" if meta["verified"] else "uncertain"
        draw_example(hit["path"], hit["box"], out_name, label)

        dep_session = confirmed_by_end.get(dep_end)
        hole_url = None
        if dep_session:
            hole_url = rel_url(dep_session["peak_path"], HOLE_CAM_DIR, "/hole-cam/static")

        examples.append({
            "departure_end": dep_end,
            "score": hit["score"],
            "lag_seconds": hit["lag_seconds"],
            "verified": meta["verified"],
            "note": meta["note"],
            "shed_example_url": f"/app/naturalist/owlv2/examples/{out_name}",
            "hole_departure_url": hole_url,
        })

    examples.sort(key=lambda e: (-e["verified"], -e["score"]))

    findings = {
        "generated_at": lag_data.get("generated_at"),
        "hole_confirmation": {
            "candidate_sessions": n_candidates,
            "confirmed": len(confirmed),
            "rejected": confirmed_data["rejected_count"],
            "score_histogram": histogram(confirmed_scores,
                                         [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]),
        },
        "shed_correlation": {
            "n_departures": params["n_departures"],
            "window_seconds": params["window_seconds"],
            "frames_per_window": params["frames_per_window"],
            "hit_threshold": params["hit_threshold"],
            "departure_hit_rate": dep_hit_rate,
            "baseline_hit_rate": base_hit_rate,
            "lift": dep_hit_rate / base_hit_rate if base_hit_rate > 0 else None,
            "departure_score_histogram": histogram(
                dep_scores, [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]),
            "baseline_score_histogram": histogram(
                base_scores, [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]),
        },
        "lag_distribution": {
            "n": len(lags),
            "median_seconds": sorted(lags)[len(lags) // 2] if lags else None,
            "mean_seconds": sum(lags) / len(lags) if lags else None,
            "max_seconds": max(lags) if lags else None,
            "window_censored_note": (
                "The analysis window was capped at 600s, so this distribution's "
                "tail is truncated — some real sightings may occur later than shown."
            ),
            "histogram": histogram(lags, [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600]),
        },
        "examples": examples,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FINDINGS_PATH.write_text(json.dumps(findings, indent=2))
    print(f"wrote {FINDINGS_PATH}", file=sys.stderr)
    print(f"wrote {len(examples)} example images to {EXAMPLES_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
