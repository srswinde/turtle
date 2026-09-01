"""Train the Cassini pose model against a DeepLabCut project exported by
``pose_export_dlc.py``. Manual, offline -- same precedent as
``scripts/owlv2_*.py`` and the OWLv2 investigation scripts; not wired into
any retrain UI yet (see naturalist memory: validate a new perception signal
offline before deciding how/whether to wire it into anything live).

    PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python scripts/pose_train.py \\
        --config /mnt/nfs/models/pose/cassini-pose-scott-<date>/config.yaml

Defaults target a real training run. For a quick plumbing smoke test
against a handful of labeled frames (not a usable model, just confirms the
pipeline runs end-to-end), pass small values, e.g.
``--epochs 2 --save-epochs 1 --display-iters 1``.
"""
from __future__ import annotations

import argparse


def train(config: str, *, epochs: int, save_epochs: int, display_iters: int) -> None:
    import deeplabcut

    deeplabcut.create_training_dataset(config, engine=deeplabcut.Engine.PYTORCH, userfeedback=False)
    deeplabcut.train_network(config, epochs=epochs, save_epochs=save_epochs,
                             display_iters=display_iters)


def main() -> None:
    ap = argparse.ArgumentParser(description="Train the Cassini DLC pose model.")
    ap.add_argument("--config", required=True, help="path to the DLC project's config.yaml")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--save-epochs", type=int, default=25)
    ap.add_argument("--display-iters", type=int, default=100)
    args = ap.parse_args()
    train(args.config, epochs=args.epochs, save_epochs=args.save_epochs,
         display_iters=args.display_iters)


if __name__ == "__main__":
    main()
