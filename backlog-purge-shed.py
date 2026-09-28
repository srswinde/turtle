"""One-time sweep of the shed-cam backlog.

cleanup-shed-cam.py's daily cron run covers a sliding 90-day band
[now-270d, now-180d) every time it runs -- it never reaches anything older
than 270 days. As of 2026-09-11 that's ~7.5M rows (of 12.75M total), ~6.6M of
them with no non-"chair" resnet detection and therefore deletable by the
exact same criterion the daily script already uses. This walks
clean_overtime() hour-by-hour from the oldest existing row up to the 270-day
cutoff, so the daily cron can take over routine maintenance once it's done.

This is a big walk (~530 days x 24 = ~12.7k one-hour windows) -- expect it to
take a while (the hole-cam sweep runs ~24 min per 100k-row chunk, dominated
by per-file NFS stat() calls; this one is windowed differently but expect a
similarly long multi-day background run). Resumable: progress (next start
hour + running totals) is written to STATE_PATH after every window. Flock-
guarded against overlapping with itself or the daily cron's shed run (same
lock file).

Usage:
    python backlog-purge-shed.py            # real run
    python backlog-purge-shed.py --dry-run  # report one window, no deletes
    python backlog-purge-shed.py --reset    # clear saved progress and restart from the oldest row
"""
import datetime as dt
import fcntl
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / ".backlog-purge-shed.state.json"
LOG_PATH = Path.home() / "nfs-backlog-shed.log"
LOCK_PATH = Path("/tmp/nfs-cleanup-shed.lock")  # same lock nfs-cleanup.sh uses for the daily shed run

spec = importlib.util.spec_from_file_location("cleanup_shed_cam", HERE / "cleanup-shed-cam.py")
cleanup_shed_cam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup_shed_cam)


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%F %T}  {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"start_ms": None, "windows": 0, "total_deleted": 0, "total_freed": 0}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


def main(dry_run: bool) -> None:
    from turtle_db import db

    cutoff = dt.datetime.now() - dt.timedelta(days=270)
    state = load_state()

    if state["start_ms"] is None:
        session = db.mksession()
        oldest = (
            session.query(db.shed_camera.timestamp)
            .order_by(db.shed_camera.timestamp.asc())
            .first()
        )
        session.close()
        if not oldest:
            log("shed_camera is empty, nothing to do")
            return
        # align to the top of the hour so windows don't drift
        start_dt = dt.datetime.fromtimestamp(oldest[0] / 1000).replace(minute=0, second=0, microsecond=0)
        state["start_ms"] = int(start_dt.timestamp() * 1000)
        save_state(state)

    start = dt.datetime.fromtimestamp(state["start_ms"] / 1000)
    log(f"=== starting from {start} (cutoff {cutoff}) dry_run={dry_run}, "
        f"resuming after {state['windows']} prior windows, "
        f"{state['total_deleted']} deleted so far, {state['total_freed']/1e9:.2f}GB freed so far ===")

    report_every = 24  # one log line per simulated day, not per hour
    while start < cutoff:
        n_deleted, bytes_freed = cleanup_shed_cam.clean_overtime(start, 1, dry_run=dry_run)
        state["windows"] += 1
        state["total_deleted"] += n_deleted
        state["total_freed"] += bytes_freed
        next_start = start + dt.timedelta(hours=1)
        if not dry_run:
            state["start_ms"] = int(next_start.timestamp() * 1000)
            save_state(state)
        if state["windows"] % report_every == 0 or dry_run:
            log(f"window {state['windows']}: through {start}  "
                f"running_total_deleted={state['total_deleted']}  "
                f"running_total_freed={state['total_freed']/1e9:.2f}GB")
        if dry_run:
            break  # one window is enough to sample
        start = next_start

    if start >= cutoff:
        log(f"=== reached the 270-day cutoff ({cutoff}); daily cron takes over from here. "
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
        log("SKIP: shed cleanup lock held (daily cron or another sweep is running)")
        sys.exit(0)
    try:
        main(dry_run="--dry-run" in sys.argv)
    finally:
        fcntl.flock(lockfile, fcntl.LOCK_UN)
