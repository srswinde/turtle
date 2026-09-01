"""Training orchestration for the web `detect` module.

Launches the training subprocess (modules.detect.train) so it runs isolated
from the web event loop, and exposes its status/logs by reading the shared
status + log files. No torch import here.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

from app.settings import settings

from .registry import active_model_path


def _status_file() -> Path:
    return Path(settings.model_root) / "train_status.json"


def _log_file() -> Path:
    return Path(settings.model_root) / "train.log"


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned elsewhere
    except OSError:
        return False
    return True


def _read_status() -> dict:
    sf = _status_file()
    if not sf.is_file():
        return {"state": "idle"}
    try:
        return json.loads(sf.read_text())
    except ValueError:
        return {"state": "unknown"}


def is_running() -> bool:
    st = _read_status()
    return st.get("state") in ("starting", "running") and _pid_alive(st.get("pid"))


def status() -> dict:
    st = _read_status()
    running = st.get("state") in ("starting", "running")
    if running and not _pid_alive(st.get("pid")):
        # process died without writing a terminal state
        st = {**st, "state": "error", "error": st.get("error") or "process exited unexpectedly"}
    st["active_model"] = active_model_path()
    return st


def logs(lines: int = 200) -> str:
    lf = _log_file()
    if not lf.is_file():
        return ""
    content = lf.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:])


def start(*, since: str | None = None, num_finetunes: int | None = None,
          num_samples: int | None = None, output_dir: str | None = None) -> dict:
    """Begin a training run.

    In Docker the web app is torch-free, so when ``ml_service_url`` is set this
    delegates to the isolated ml service; otherwise it launches locally (dev).
    """
    if settings.ml_service_url:
        import requests

        resp = requests.post(
            settings.ml_service_url.rstrip("/") + "/train",
            json={"since": since, "num_finetunes": num_finetunes,
                  "num_samples": num_samples, "output_dir": output_dir},
            timeout=15,
        )
        if resp.status_code == 409:
            raise RuntimeError("A training run is already in progress")
        resp.raise_for_status()
        return status()

    return _start_local(
        since=since, num_finetunes=num_finetunes,
        num_samples=num_samples, output_dir=output_dir,
    )


def _start_local(*, since: str | None = None, num_finetunes: int | None = None,
                 num_samples: int | None = None, output_dir: str | None = None) -> dict:
    if is_running():
        raise RuntimeError("A training run is already in progress")

    model_root = Path(settings.model_root)
    model_root.mkdir(parents=True, exist_ok=True)
    out_dir = output_dir or str(model_root)
    n_finetunes = num_finetunes or settings.train_default_finetunes
    n_samples = num_samples or settings.train_default_samples
    last_model = active_model_path()
    status_file = _status_file()

    cmd = [
        sys.executable, "-m", "modules.detect.train",
        "--output-dir", out_dir,
        "--last-model", last_model,
        "--image-base", settings.train_image_base,
        "--status-file", str(status_file),
        "--num-finetunes", str(n_finetunes),
        "--num-samples", str(n_samples),
    ]
    if since:
        cmd += ["--since", since]

    log_fh = open(_log_file(), "w")  # noqa: SIM115 - handed to the child
    proc = subprocess.Popen(
        cmd, stdout=log_fh, stderr=subprocess.STDOUT,
        cwd=os.getcwd(), env=os.environ.copy(),
    )
    # Record pid up front; the child merges its own progress into this file.
    status_file.write_text(json.dumps({
        "state": "starting",
        "pid": proc.pid,
        "started_at": datetime.datetime.now().isoformat(),
        "params": {"num_finetunes": n_finetunes, "num_samples": n_samples,
                   "last_model": last_model, "since": since, "output_dir": out_dir},
        "progress": {"current": 0, "total": n_finetunes},
    }))
    return status()
