"""Persistent-session Amcrest HTTP client.

amcrest.http.Http._command() opens a brand-new requests.Session() -- fresh
TCP connection + full HTTP Digest auth login/logout -- for EVERY single CGI
call (confirmed by reading the installed amcrest package's source directly,
2026-08-24). The production ingest loop (shed_cam.py/hole_cam.py, host
systemd, NOT this web app) calls take_image() roughly every ~2s, each doing 3
separate CGI calls, so that's ~1.5 fresh logins/sec from routine polling
alone.

Diagnosed 2026-08-24 via the shed camera's own onboard event log
(AmcrestCamera.camera.log_show()): shed-cam logged ~960 login/logout events
in 10 minutes vs. hole-cam's 2, and swahle-shed-cam.service's own journal
showed hundreds of ConnectTimeout retries/hour against ITSELF -- this churn
is a real, pre-existing contributor to that camera's chronic capacity strain
(separate from, and predating, the same-day live-video RTSP work -- see
swahle-web-deploy / naturalist-perception-pipeline memory).

This web app's own camera usage (modules/cameras/module.py's snapshot route,
modules/naturalist/acquisition.py's ShedScanner PTZ checks) is much
lower-volume than the ingest loop, but applying the same fix here keeps
behavior consistent and removes it as a contributor too.

PersistentApiWrapper reuses ONE requests.Session() across calls instead of
one-per-call, so TCP connections (and the socket-level keepalive options
amcrest already configures but never gets to use, since the session housing
them is torn down again immediately after each call) actually stay alive
between requests. Failures are handled adaptively -- a request that errors on
the shared session gets the session torn down and recreated before amcrest's
own retry loop tries again -- rather than via a fixed refresh timer (this
camera is independently, unpredictably flaky regardless of idle time; see
docstring above).

Sibling copy: shed-cam/ipcamera/persistent_amcrest.py (separate repo, the
production ingest side) -- keep both in sync if this needs further changes.
"""
from __future__ import annotations

import logging
import threading

import requests
from amcrest import ApiWrapper
from amcrest import AmcrestCamera as _AmcrestCamera
from amcrest.exceptions import CommError, LoginError
from amcrest.http import _KEEPALIVE_OPTS, SOHTTPAdapter

log = logging.getLogger("swahle.cameras.persistent_amcrest")


class PersistentApiWrapper(ApiWrapper):
    """Drop-in replacement for amcrest's ApiWrapper (what AmcrestCamera(...).camera
    returns) that reuses one keep-alive HTTP session per instance instead of
    opening a fresh one for every call. Everything else -- retry count,
    per-call timeout, digest auth token, 401 handling -- is unchanged from
    upstream; only the session's lifetime moves from per-call to
    per-instance, with automatic replacement on failure.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._persistent_session_lock = threading.Lock()
        self._persistent_session: requests.Session | None = None

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.mount(
            f"{self._protocol}://",
            SOHTTPAdapter(socket_options=_KEEPALIVE_OPTS),
        )
        return session

    def _get_session(self) -> requests.Session:
        with self._persistent_session_lock:
            if self._persistent_session is None:
                self._persistent_session = self._new_session()
            return self._persistent_session

    def _reset_session(self) -> None:
        with self._persistent_session_lock:
            if self._persistent_session is not None:
                self._persistent_session.close()
            self._persistent_session = self._new_session()

    # Faithful port of amcrest.http.Http._command, with the per-call
    # `with requests.Session() as session:` replaced by the persistent
    # session above (recreated on any failure instead of on a fixed timer).
    def _command(self, cmd, retries=None, timeout_cmd=None, stream=False):
        url = f"{self._protocol}://{self._host}:{self._port}/cgi-bin/{cmd}"
        with self._cmd_id_lock:
            cmd_id = self._cmd_id = self._cmd_id + 1
        if retries is None:
            retries = self._retries_default
        timeout = timeout_cmd or self._timeout_default

        for loop in range(1, 2 + retries):
            session = self._get_session()
            try:
                resp = session.get(
                    url, auth=self._token, stream=stream,
                    timeout=timeout, verify=self._verify,
                )
                if resp.status_code == 401:
                    log.debug("%s Query %i: Unauthorized (401)", self, cmd_id)
                    self._token = None
                    self._reset_session()
                    raise LoginError()
                resp.raise_for_status()
            except requests.RequestException as error:
                if loop > retries:
                    raise CommError(error) from error
                log.warning("%s Trying again due to error: %s (recreating session)",
                            self, error)
                self._reset_session()
                continue
            else:
                return resp
        raise CommError(f"{self}: exhausted retries for {cmd}")  # unreachable


def PersistentAmcrestCamera(host, port, user, password, **kwargs):
    """Same call signature as amcrest.AmcrestCamera(...), but .camera is a
    PersistentApiWrapper instead of the vanilla per-call-session ApiWrapper."""
    cam = _AmcrestCamera(host, port, user, password, **kwargs)
    cam.camera = PersistentApiWrapper(
        host=host, port=port, user=user, password=password, **kwargs,
    )
    return cam
