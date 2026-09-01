#!/usr/bin/env python3
"""One-time backfill: make the naturalist yard-scan frames readable to `scott`.

The yard-scan writer (``camera.py`` ``SnapshotWrapper.save_compress``, called
from naturalist ``ShedScanner``) runs as root inside the swahle-web container,
so the day-dirs it created land ``root:root`` with a restrictive inherited ACL
and ``swahle-mirror.service`` (user ``scott``) can't read the ``yardscan__*``
frames -- they drop out of the slideshow.

Rather than walk the tree (tens of millions of frames over NFS, hours), we take
the exact frame list from the DB (``shed_detection.path`` /
``shed_sighting.path``) and fix precisely those files plus their parent
day-dirs -- a few hundred paths, seconds.

    sudo /home/scott/anaconda3/bin/python \
        swahle/deploy/scripts/fix_cam_frame_perms.py

Override the DB endpoint with SWAHLE_DB_{HOST,PORT,USER,PASS,NAME} if it isn't
the dev default (127.0.0.1:3306 scott/scott/turtle).
"""
from __future__ import annotations

import os
import pwd
import subprocess

import pymysql

DB = dict(
    host=os.environ.get("SWAHLE_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("SWAHLE_DB_PORT", "3306")),
    user=os.environ.get("SWAHLE_DB_USER", "scott"),
    password=os.environ.get("SWAHLE_DB_PASS", "scott"),
    database=os.environ.get("SWAHLE_DB_NAME", "turtle"),
)

SCOTT = pwd.getpwnam("scott")


def _fix(path: str, mode: int) -> bool:
    subprocess.run(["setfacl", "-b", path], stderr=subprocess.DEVNULL, check=False)
    try:
        os.chown(path, SCOTT.pw_uid, SCOTT.pw_gid)
        os.chmod(path, mode)
        return True
    except OSError as exc:
        print(f"  skip {path}: {exc}")
        return False


def main() -> None:
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path FROM shed_detection WHERE path IS NOT NULL "
                "UNION SELECT path FROM shed_sighting WHERE path IS NOT NULL"
            )
            paths = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    dirs: set[str] = set()
    nf = 0
    for p in paths:
        if not p:
            continue
        dirs.add(os.path.dirname(p))
        if os.path.exists(p):
            nf += _fix(p, 0o644)

    nd = sum(_fix(d, 0o775) for d in sorted(dirs) if os.path.isdir(d))
    print(f"fixed {nf} file(s) across {nd} day-dir(s) (of {len(paths)} DB paths)")


if __name__ == "__main__":
    main()
