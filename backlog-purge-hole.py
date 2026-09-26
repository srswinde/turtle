"""One-time sweep of the hole-cam backlog.

cleanup-hole-cam.py's daily cron run only ever processes the oldest 100k
rows *past* `now - 180 days` -- it never reaches anything older than that
cutoff. As of 2026-09-11 that's ~10.6M rows (of 16.3M total), ~10.0M of them
with no resnet detection and therefore deletable by the exact same criterion
the daily script already uses. This walks clean() forward from the oldest
existing row up to the 180-day cutoff, chunk by chunk, so the daily cron can
take over routine maintenance once it's done.

Resumable: progress (next start timestamp + running totals) is written to
STATE_PATH after every chunk. A restart picks up where it left off instead
of rescanning from the beginning. Flock-guarded against overlapping with
itself or with the daily cron's hole run (same lock file).

Usage:
    python backlog-purge-hole.py            # real run
    python backlog-purge-hole.py --dry-run  # report only, no deletes
    python backlog-purge-hole.py --reset    # clear saved progress and restart from the oldest row
"""
import datetime as dt
import fcntl
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / ".backlog-purge-hole.state.json"
LOG_PATH = Path.home() / "nfs-backlog-hole.log"
LOCK_PATH = Path("/tmp/nfs-cleanup-hole.lock")  # same lock nfs-cleanup.sh uses for the daily hole run

spec = importlib.util.spec_from_file_location("cleanup_hole_cam", HERE / "cleanup-hole-cam.py")
cleanup_hole_cam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup_hole_cam)


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%F %T}  {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"start_ms": None, "chunks": 0, "total_deleted": 0, "total_freed": 0}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


def main(dry_run: bool) -> None:
    from turtle_db import db

    cutoff = dt.datetime.now() - dt.timedelta(days=180)
    state = load_state()

    if state["start_ms"] is None:
        session = db.mksession()
        oldest = (
            session.query(db.hole_camera.timestamp)
            .order_by(db.hole_camera.timestamp.asc())
            .first()
        )
        session.close()
        if not oldest:
            log("hole_camera is empty, nothing to do")
            return
        state["start_ms"] = oldest[0]
        save_state(state)

    start = dt.datetime.fromtimestamp(state["start_ms"] / 1000)
    log(f"=== starting from {start} (cutoff {cutoff}) dry_run={dry_run}, "
        f"resuming after {state['chunks']} prior chunks, "
        f"{state['total_deleted']} deleted so far, {state['total_freed']/1e9:.2f}GB freed so far ===")

    while start < cutoff:
        ts_end, n_deleted, bytes_freed = cleanup_hole_cam.clean(start, dry_run=dry_run)
        if ts_end is None:
            log("no more rows past the current start -- caught up early")
            break
        state["chunks"] += 1
        state["total_deleted"] += n_deleted
        state["total_freed"] += bytes_freed
        if not dry_run:
            # Only persist progress on a real run -- a dry-run must be able to
            # be re-run from the same starting point without disturbing the
            # real sweep's resume point.
            state["start_ms"] = int(ts_end.timestamp() * 1000) + 1
            save_state(state)
        log(f"chunk {state['chunks']}: through {ts_end}  deleted={n_deleted}  "
            f"freed={bytes_freed/1e9:.2f}GB  "
            f"running_total_deleted={state['total_deleted']}  "
            f"running_total_freed={state['total_freed']/1e9:.2f}GB")
        if dry_run:
            break  # one chunk is enough to sample; a real dry-run sweep would just re-report the same chunk
        start = dt.datetime.fromtimestamp(state["start_ms"] / 1000)

    if start >= cutoff:
        log(f"=== reached the 180-day cutoff ({cutoff}); daily cron takes over from here. "
            f"total deleted={state['total_deleted']}, total freed={state['total_freed']/1e9:.2f}GB ===")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        STATE_PATH.unlink(missing_ok=True)
        log("state reset")
        sys.exit(0)
    lockfile = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("SKIP: hole cleanup lock held (daily cron or another sweep is running)")
        sys.exit(0)
    try:
        main(dry_run="--dry-run" in sys.argv)
    finally:
        fcntl.flock(lockfile, fcntl.LOCK_UN)
