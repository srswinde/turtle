"""Amcrest camera capture (ported from shed-cam/ipcamera/camera.py).

No ML imports here so the web app's live-snapshot route can use it without
pulling torch. Configured from a plain dict (see modules.cameras.config).
"""
from __future__ import annotations

import datetime
import logging
import os
import shlex
import subprocess
from io import BytesIO
from pathlib import Path

from amcrest import exceptions
# Persistent-session client (2026-08-24): the vanilla amcrest AmcrestCamera
# opens a fresh TCP connection + full auth login for every single CGI call --
# see persistent_amcrest.py docstring for the shed-cam capacity investigation
# that motivated this.
from .persistent_amcrest import PersistentAmcrestCamera as AmcrestCamera
from PIL import Image

log = logging.getLogger("swahle.cameras.camera")


class Camera:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.ip = config["ip"]
        self.port = config["port"]
        self.username = config["username"]
        self.password = config["password"]
        self.basedir = config.get("basedir", "/mnt/nfs")
        self.latest_path = config.get("latest_path")
        self.amcrest = AmcrestCamera(self.ip, self.port, self.username, self.password).camera

    def record(self, time_seconds: int = 10, path: Path | None = None,
               basedir: str | None = None):
        """Record an RTSP clip to a date-based path via ffmpeg."""
        if path is None:
            ts = datetime.datetime.now()
            root = basedir or f"{self.basedir}/video"
            path = Path(root) / f"{ts.year}/{ts.month}/{ts.day}/{ts.timestamp()}.mp4"
        rtsp_port = self.config.get("rtsp_port", "554")
        channel = self.config.get("channel", "1")
        subtype = self.config.get("subtype", "0")
        path.parent.mkdir(parents=True, exist_ok=True)
        url = (f"'rtsp://{self.username}:{self.password}@{self.ip}:{rtsp_port}"
               f"/cam/realmonitor?channel={channel}&subtype={subtype}'")
        cmd = f"ffmpeg -i {url} -t {time_seconds} -c:v copy -c:a copy {path}"
        log.info("Recording to %s", path)
        return subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def goto_pantilt(self, pan, tilt, ntries: int = 3) -> None:
        error_count = 0
        while True:
            try:
                self.amcrest.ptz_control_command(
                    action="start", code="PositionABS", arg1=pan, arg2=tilt, arg3=0)
                import time
                time.sleep(4.0)
                return
            except exceptions.CommError as error:
                import time
                time.sleep(10.0)
                error_count += 1
                log.error("Failed to move: %s", error)
            if error_count > ntries:
                raise RuntimeError("Could not move to position (communication error).")

    def snapshot_bytes(self, channel: int = 1) -> bytes:
        """Raw JPEG bytes for a single frame (used by the live-view route)."""
        return self.amcrest.snapshot(channel=channel).read()

    def take_image(self, basename: str, basedir: str | None = None, save: bool = False):
        """Capture a frame, returning a SnapshotWrapper (or False on error)."""
        motion = human = None
        try:
            motion = bool(self.amcrest.is_motion_detected)
            human = bool(self.amcrest.is_human_detected)
        except Exception as exc:  # noqa: BLE001
            log.error("Error checking motion/human: %s", exc)
        try:
            snapshot = self.amcrest.snapshot(channel=1)
        except Exception as exc:  # noqa: BLE001
            log.error("Error taking snapshot: %s", exc)
            return False
        sw = SnapshotWrapper(
            snapshot, motion, human, self.config,
            basedir=basedir or self.basedir, basename=basename, save=save,
        )
        log.info("Captured %s", sw.path)
        return sw


def _mkdirs_readable(path: Path, stop: Path) -> None:
    """``mkdir -p`` ``path``, then chmod every segment from ``path`` up to (not
    including) ``stop`` to 0o755. The yard-scan writer (naturalist ShedScanner)
    runs as root inside the swahle-web container; without this the day-dirs it
    creates land with a restrictive inherited ACL and the mirror service (user
    ``scott``) can't read the frames. Best-effort -- chmod on a segment we
    don't own just raises and is ignored."""
    path.mkdir(parents=True, exist_ok=True)
    stop = stop.resolve()
    cur = path.resolve()
    while cur != stop and stop in cur.parents:
        try:
            os.chmod(cur, 0o755)
        except OSError:
            pass
        cur = cur.parent


def _chmod_readable(path: Path) -> None:
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


class SnapshotWrapper:
    def __init__(self, snapshot, motion, human, config: dict, timestamp=None,
                 basedir: str = "/mnt/nfs/shed-cam", save: bool = True,
                 basename: str | None = None) -> None:
        self.config = config
        self.is_saved = False
        timestamp = timestamp or datetime.datetime.now()
        fname = basename or "snapshot"
        self.basedir = Path(basedir)
        self.path = self.basedir / (
            f"{timestamp.year}/{timestamp.month}/{timestamp.day}/{fname}_{timestamp.timestamp()}.jpg"
        )
        self.data = snapshot.read()
        self.timestamp = timestamp
        self.motion = motion
        self.human = human
        if save:
            self.save(self.data)

    def save(self, imdata: bytes | None = None) -> None:
        _mkdirs_readable(self.path.parent, self.basedir)
        with open(self.path, "wb") as fh:
            fh.write(self.data if imdata is None else imdata)
        _chmod_readable(self.path)
        self.is_saved = True

    def save_latest(self) -> None:
        latest = self.config.get("latest_path")
        if not latest:
            return
        with open(latest, "wb") as fh:
            fh.write(self.data)

    def pil_image(self) -> Image.Image:
        return Image.open(BytesIO(self.data))

    def save_compress(self, reduce_factor: int, quality: int, pil_image=None) -> None:
        pil_image = pil_image or self.pil_image()
        w, h = pil_image.size
        new_size = (int(w / reduce_factor), int(h / reduce_factor))
        _mkdirs_readable(self.path.parent, self.basedir)
        pil_image.resize(new_size, Image.LANCZOS).save(
            self.path, format="JPEG", quality=quality)
        _chmod_readable(self.path)
        self.is_saved = True
        log.info("Saved compressed image to %s", self.path)
