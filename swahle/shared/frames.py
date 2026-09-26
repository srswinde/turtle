"""Single-frame grabs from the MediaMTX republish.

The point: nothing except MediaMTX should hold a connection to a camera. The
cameras (esp. the shed Amcrest) fall over when a second client hits
``snapshot.cgi`` while MediaMTX is already pulling RTSP -- confirmed 2026-09-05
when a manual PTZ sweep + the ShedScanner poll together rebooted the shed cam.
So analysis code calls :func:`grab_jpeg` here instead of ``Camera.take_image``:
MediaMTX keeps one upstream RTSP connection, we pull a keyframe off *its*
republish over the docker bridge.

Requires ``ffmpeg`` on PATH (added to Dockerfile.web) and MediaMTX's RTSP
server enabled (``rtsp: true`` in mediamtx.yml). PTZ control still has to go
straight to the camera -- RTSP/HLS can't carry ONVIF -- but that's an
occasional control call, not frame polling, and was never the load problem.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

from app.settings import settings

log = logging.getLogger("swahle.frames")


class FrameGrabError(RuntimeError):
    """A frame could not be pulled from MediaMTX (timeout, ffmpeg error,
    stream not ready, misconfigured base URL)."""


# One in-flight grab per stream name -- ffmpeg opening the same RTSP path
# twice at once just wastes a camera slot and races.
_locks: dict[str, asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    lock = _locks.get(name)
    if lock is None:
        lock = _locks[name] = asyncio.Lock()
    return lock


async def grab_jpeg(path_name: str, *, timeout: float | None = None) -> bytes:
    """Return JPEG bytes for one frame of the MediaMTX stream ``path_name``
    (e.g. ``"shed"`` / ``"shed_hi"``). Raises :class:`FrameGrabError`.

    ``sourceOnDemand`` paths take a beat to spin the camera up on the first
    call after an idle gap; ``timeout`` should comfortably exceed
    ``sourceOnDemandStartTimeout`` in mediamtx.yml (15s for shed).
    """
    base = settings.mediamtx_rtsp_url.rstrip("/")
    if not base:
        raise FrameGrabError("settings.mediamtx_rtsp_url is empty")
    if shutil.which("ffmpeg") is None:
        raise FrameGrabError("ffmpeg not found on PATH")

    url = f"{base}/{path_name}"
    timeout = timeout if timeout is not None else settings.frame_grab_timeout
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", url,
        "-frames:v", "1", "-q:v", "3", "-f", "image2", "pipe:1",
    ]

    async with _lock(path_name):
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise FrameGrabError(f"ffmpeg timed out after {timeout:.0f}s on {url}") from exc

    if proc.returncode != 0 or not out:
        tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["(no stderr)"]
        raise FrameGrabError(f"ffmpeg exit {proc.returncode} on {url}: {tail[0]}")
    return out
