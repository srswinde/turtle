#!/bin/bash
# Scheduled /mnt/nfs cleanup -- runs the legacy cam-frame pruners on a
# schedule so the NFS share doesn't fill (it did, 2026-09-01, taking the
# hole-cam ingest down). Each target has its own flock so a long shed run
# (~10h for a full pass) doesn't block the quick hole run, and an overlapping
# cron fire is skipped rather than piled on.
#
# Install (scott's crontab, no sudo needed):
#   20 1 * * *  /home/scott/git-clones/turtle_db/swahle/deploy/scripts/nfs-cleanup.sh hole
#   40 1 * * *  /home/scott/git-clones/turtle_db/swahle/deploy/scripts/nfs-cleanup.sh shed
#
# Log: ~/nfs-cleanup.log (override with $NFS_CLEANUP_LOG).
#
# NOTE: the underlying scripts only prune their own age windows
# (cleanup-hole-cam.py: no-detection frames < 180d old, 100k/run;
# cleanup-shed-cam.py: the 270->180d-ago window). Frames older than that --
# the bulk of a full disk -- are NOT touched. Widen those windows or do a
# one-time bulk purge for the historical backlog.
set -u

REPO=/home/scott/git-clones/turtle_db
PY=/home/scott/anaconda3/bin/python
LOG="${NFS_CLEANUP_LOG:-$HOME/nfs-cleanup.log}"

target="${1:-all}"
case "$target" in
  hole) scripts=(cleanup-hole-cam.py) ;;
  shed) scripts=(cleanup-shed-cam.py) ;;
  all)  scripts=(cleanup-hole-cam.py cleanup-shed-cam.py) ;;
  *) echo "usage: $0 {hole|shed|all}" >&2; exit 2 ;;
esac

export PYTHONPATH="$REPO"
export TURTLE_DB_URI="mysql+pymysql://scott:scott@127.0.0.1/turtle"

log(){ echo "$(date '+%F %T')  [$target] $*" >>"$LOG"; }

exec 9>"/tmp/nfs-cleanup-$target.lock"
if ! flock -n 9; then
  log "SKIP: previous run still holding the lock"
  exit 0
fi

avail_gib(){ echo $(( $(df --output=avail -k /mnt/nfs | tail -1) / 1024 / 1024 )); }

before=$(avail_gib)
log "=== start (free: ${before} GiB) ==="
cd "$REPO" || { log "FATAL: cannot cd $REPO"; exit 1; }

for s in "${scripts[@]}"; do
  log "--- $s ---"
  "$PY" "$s" 2>&1 | grep --line-buffered -vE '^Deleting image file |[0-9]+it/s\]' >>"$LOG"
  log "$s: exit ${PIPESTATUS[0]}"
done

after=$(avail_gib)
log "=== done (free: ${after} GiB, freed: $((after - before)) GiB) ==="
