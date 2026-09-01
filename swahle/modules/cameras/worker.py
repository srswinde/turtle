"""Camera ingest worker (ported from shed-cam/{hole_cam,shed_cam}.py).

Captures frames from the Amcrest cameras, writes images into the RAID
date-directory layout, runs the cassini (hole) / faster-rcnn detectors, and
records rows in the DB. Runs as its own process (the Compose `worker` service):

    python -m modules.cameras.worker --camera hole
    python -m modules.cameras.worker --camera shed

Verification-safety flags (write nothing to prod):
    --once       capture a single frame then exit
    --dry-run    skip all writes (DB, recording, lights, notify); still captures
                 + runs inference; images go to --outdir instead of the RAID
    --outdir     override the image base directory
    --no-ml      skip model loading (prob=-1, no detections) for structural tests
"""
from __future__ import annotations

import argparse
import datetime
import logging
import time

import requests

from app.settings import settings
from shared.db import SessionLocal
from shared.models import (
    HAS_HUMAN,
    HAS_MOTION,
    hole_camera,
    hole_camera_metadata,
    hole_camera_resnet_detection,
    shed_camera,
    shed_camera_metadata,
    shed_camera_resnet_detection,
)

from .camera import Camera

log = logging.getLogger("swahle.cameras.worker")

RESNET_MIN_PROB = 0.92
CASSINI_RECORD_PROB = 0.72


# --- motion/human enum mapping (kept per-camera-faithful) ------------------

def _hole_enum(value, yes, no, null):
    if value is None:
        return null
    return yes if value else no


def _shed_motion(val):
    # Faithful to the legacy shed loop: compares against the *string* "True",
    # so bool inputs fall through to NULL. Preserved to match stored data.
    if val == "True":
        return HAS_MOTION.YES
    if val == "False":
        return HAS_MOTION.NO
    return HAS_MOTION.NULL


def _shed_human(val):
    if val == "True":
        return HAS_HUMAN.YES
    if val == "False":
        return HAS_HUMAN.NO
    return HAS_HUMAN.NULL


def _shed_light(turn_on: bool, ip: str) -> dict:
    try:
        state = "on" if turn_on else "off"
        return requests.get(f"http://{ip}/relay/0?turn={state}", timeout=2).json()
    except requests.exceptions.RequestException as exc:
        log.error("Error communicating with shed light: %s", exc)
        return {"ison": None}


# --- hole-cam loop ----------------------------------------------------------

def run_hole(*, once: bool = False, dry_run: bool = False, use_ml: bool = True,
             outdir: str | None = None) -> None:
    config = settings.cameras["hole"]
    camera = Camera(config)
    basedir = outdir or config["basedir"]

    # Resolve the active checkpoint via the registry so retrain/rollback
    # (M5) takes effect on the next worker start.
    from modules.detect.registry import active_model_path
    model_path = active_model_path()

    detector = cassini = None
    if use_ml:
        from .cassini import CassiniClassifier
        from .detect import Detector
        detector = Detector()
        cassini = CassiniClassifier(model_path)
        log.info("Loaded models (cassini=%s)", model_path)

    recording_proc = None
    while True:
        now = datetime.datetime.now()
        after_hours = now.hour < 6 or now.hour > 20

        sw = camera.take_image("snapshot", basedir, save=False)
        if not sw:
            log.error("Could not take image")
            if once:
                return
            time.sleep(5.0)
            continue

        if not dry_run:
            sw.save_latest()

        motion = _hole_enum(sw.motion, HAS_MOTION.YES, HAS_MOTION.NO, HAS_MOTION.NULL)
        human = _hole_enum(sw.human, HAS_HUMAN.YES, HAS_HUMAN.NO, HAS_HUMAN.NULL)

        results = detector.eval(sw.data) if detector else []
        prob = cassini.prob(sw.data) if cassini else -1.0

        if use_ml and after_hours and prob < CASSINI_RECORD_PROB:
            log.info("Skipping %s (prob %.3f) after hours", sw.path, prob)
            if once:
                return
            continue

        sw.save_compress(4, quality=50)  # persist the (compressed) still

        ts_ms = int(sw.timestamp.timestamp() * 1000)
        row = dict(timestamp=ts_ms, prob=prob, path=str(sw.path),
                   model_name=model_path)
        meta = dict(timestamp=ts_ms, is_motion_detected=motion, is_human_detected=human)
        detections = [
            dict(timestamp=ts_ms, path=str(sw.path), **d)
            for d in results if d["prob"] > RESNET_MIN_PROB
        ]

        if dry_run:
            log.info("[dry-run] hole_camera row: %s", row)
            log.info("[dry-run] metadata: motion=%s human=%s", motion, human)
            log.info("[dry-run] %d resnet detection(s): %s",
                     len(detections), [d["class_name"] for d in detections])
        else:
            if prob > CASSINI_RECORD_PROB:
                if recording_proc is None or recording_proc.poll() is not None:
                    recording_proc = camera.record(60, basedir=f"{basedir}/video")
                    log.info("Started recording")
            session = SessionLocal()
            try:
                session.add(hole_camera(**row))
                session.add(hole_camera_metadata(**meta))
                for d in detections:
                    log.info("Adding detection: %s", d["class_name"])
                    session.add(hole_camera_resnet_detection(**d))
                session.commit()
            finally:
                session.close()
            # IFTTT sighting notification now fires from the shed-cam OWLv2
            # scanner (modules/naturalist/acquisition.py), not from here.

        if once:
            return
        time.sleep(0.05)


# --- shed-cam loop ----------------------------------------------------------

def run_shed(*, once: bool = False, dry_run: bool = False, use_ml: bool = True,
             outdir: str | None = None) -> None:
    config = settings.cameras["shed"]
    camera = Camera(config)
    basedir = outdir or config["basedir"]
    light_ip = config.get("light_ip")

    detector = None
    if use_ml:
        from .detect import Detector
        detector = Detector()

    recording_proc = None
    light_status = {"ison": None}
    light_time = 0.0

    while True:
        sw = camera.take_image("snapshot_", basedir, save=False)
        if not sw:
            log.error("Could not take image")
            if once:
                return
            time.sleep(5.0)
            continue

        ts_ms = int(sw.timestamp.timestamp() * 1000)
        row = dict(timestamp=ts_ms, path=str(sw.path))
        meta = dict(timestamp=ts_ms,
                    is_motion_detected=_shed_motion(sw.motion),
                    is_human_detected=_shed_human(sw.human))

        results = detector.eval(sw.data) if detector else []
        detections = [d for d in results if d["prob"] > RESNET_MIN_PROB]
        person_detected = any(d["class_name"] == "person" for d in detections)

        # save quality depends on whether a person was seen
        sw.save_compress(1, quality=90) if person_detected else sw.save_compress(3, 90)

        if dry_run:
            log.info("[dry-run] shed_camera row: %s", row)
            log.info("[dry-run] %d resnet detection(s): %s person=%s",
                     len(detections), [d["class_name"] for d in detections], person_detected)
        else:
            if person_detected and light_ip:
                light_status = _shed_light(True, light_ip)
                light_time = time.time()
                if recording_proc is None or recording_proc.poll() is not None:
                    recording_proc = camera.record(60)
                    log.info("Started recording")
            if light_status.get("ison") and time.time() - light_time > 60 and light_ip:
                light_status = _shed_light(False, light_ip)

            session = SessionLocal()
            try:
                session.add(shed_camera(**row))
                session.add(shed_camera_metadata(**meta))
                for d in detections:
                    session.add(shed_camera_resnet_detection(
                        timestamp=ts_ms, path=str(sw.path), **d))
                session.commit()
            finally:
                session.close()

        if once:
            return
        time.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Swahle camera ingest worker.")
    parser.add_argument("--camera", choices=["hole", "shed"], required=True)
    parser.add_argument("--once", action="store_true", help="capture one frame then exit")
    parser.add_argument("--dry-run", action="store_true", help="skip all writes/side-effects")
    parser.add_argument("--no-ml", action="store_true", help="skip model loading")
    parser.add_argument("--outdir", default=None, help="override image base directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    runner = run_hole if args.camera == "hole" else run_shed
    runner(once=args.once, dry_run=args.dry_run, use_ml=not args.no_ml, outdir=args.outdir)


if __name__ == "__main__":
    main()
