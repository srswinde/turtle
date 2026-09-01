"""Cassini retraining subprocess (the `ml` job).

Run standalone so training is isolated from the web app (never blocks the
gallery). Reports progress to a JSON status file the web `detect` module polls::

    python -m modules.detect.train --last-model <p> --output-dir <d> \
        --status-file <f> --image-base http://web:8000/hole-cam/static

Reuses cassini_detect.Pretrained for identical finetune behaviour, but drives
the loop here so per-finetune progress + loss can be streamed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import tempfile
import traceback
from pathlib import Path

log = logging.getLogger("swahle.detect.train")


def _write_status(status_file: Path, **updates) -> dict:
    """Merge updates into the status file atomically."""
    current = {}
    if status_file.is_file():
        try:
            current = json.loads(status_file.read_text())
        except ValueError:
            current = {}
    current.update(updates)
    fd, tmp = tempfile.mkstemp(dir=status_file.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(current, fh, default=str)
    os.replace(tmp, status_file)
    return current


def _labeled_images(since: datetime.datetime, image_base: str):
    import pandas as pd

    from shared.db import SessionLocal
    from shared.models import HAS_TURTLE, hole_camera

    session = SessionLocal()
    try:
        query = (
            session.query(hole_camera)
            .filter(hole_camera.timestamp / 1000 > since.timestamp())
            .filter(hole_camera.hasTurtle != HAS_TURTLE.NULL)
        )
        data = pd.read_sql(query.statement, query.session.bind)
    finally:
        session.close()

    data["url"] = data["path"].str.replace("/mnt/nfs/hole-cam", image_base, regex=False)
    data["hasTurtle"] = data["hasTurtle"] == HAS_TURTLE.YES
    return data


def run(*, since, output_dir, num_finetunes, num_samples, last_model,
        image_base, status_file, bias_recent=False) -> None:
    import torch
    from cassini_detect.pretrained_models import Pretrained

    status_file = Path(status_file)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    _write_status(
        status_file,
        state="running",
        started_at=datetime.datetime.now().isoformat(),
        finished_at=None,
        error=None,
        final_model=None,
        progress={"current": 0, "total": num_finetunes},
        last_loss=None,
        params={"num_finetunes": num_finetunes, "num_samples": num_samples,
                "last_model": last_model, "since": since.isoformat()},
    )

    try:
        data = _labeled_images(since, image_base)
        log.info("Training on %d labeled images", len(data))
        if len(data) < num_samples:
            raise RuntimeError(
                f"Only {len(data)} labeled images; need >= {num_samples}")

        if not Path(last_model).is_file():
            raise FileNotFoundError(f"last_model not found: {last_model}")

        pret = Pretrained(linear_layer=last_model)
        criterion = torch.nn.MSELoss()
        optimizer = torch.optim.SGD(pret.linear_layer.parameters(), lr=0.001, momentum=0.01)

        run_name = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        date_dir = Path(output_dir) / run_name
        date_dir.mkdir(parents=True, exist_ok=True)
        final_model = None

        for ii in range(num_finetunes):
            weights = None
            if bias_recent:
                weights = (data["timestamp"] - data["timestamp"].min()) + 1
                weights = weights / weights.sum()
            sample = data.sample(num_samples, weights=weights)
            train_dl = pret.create_dataloader(sample)
            stats = pret.training_loop(train_dl, criterion, optimizer)

            model_dir = date_dir / f"model_{ii}"
            model_dir.mkdir(exist_ok=True)
            final_model = model_dir / "model.pth"
            torch.save(pret.linear_layer, final_model)
            sample.to_pickle(model_dir / "sample.pkl")
            stats.to_pickle(model_dir / "training_info.pkl")

            last_loss = float(stats["loss"].iloc[-1]) if len(stats) else None
            _write_status(
                status_file,
                progress={"current": ii + 1, "total": num_finetunes},
                last_loss=last_loss,
                run=run_name,
                final_model=str(final_model),
            )
            log.info("finetune %d/%d loss=%.4f", ii + 1, num_finetunes, last_loss or -1)

        _write_status(
            status_file,
            state="done",
            finished_at=datetime.datetime.now().isoformat(),
            final_model=str(final_model) if final_model else None,
        )
        log.info("Training complete: %s", final_model)
    except Exception as exc:  # noqa: BLE001
        _write_status(
            status_file,
            state="error",
            finished_at=datetime.datetime.now().isoformat(),
            error=f"{exc}\n{traceback.format_exc()}",
        )
        log.error("Training failed: %s", exc)
        raise


def main() -> None:
    from dateutil import parser as dateparser

    ap = argparse.ArgumentParser(description="Cassini retraining job.")
    ap.add_argument("--since", default=None)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--num-finetunes", type=int, default=10)
    ap.add_argument("--num-samples", type=int, default=100)
    ap.add_argument("--last-model", required=True)
    ap.add_argument("--image-base", required=True)
    ap.add_argument("--status-file", required=True)
    ap.add_argument("--bias-recent", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    since = (dateparser.parse(args.since) if args.since
             else datetime.datetime.now() - datetime.timedelta(days=90))
    run(
        since=since, output_dir=args.output_dir, num_finetunes=args.num_finetunes,
        num_samples=args.num_samples, last_model=args.last_model,
        image_base=args.image_base, status_file=args.status_file,
        bias_recent=args.bias_recent,
    )


if __name__ == "__main__":
    main()
