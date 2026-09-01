"""Thin wrapper over the cassini_detect VGG classifier.

Reuses the existing ``cassini_detect.pretrained_models.Pretrained`` so ingest
prob values are identical to the legacy pipeline. When the detect/ml module
lands (M5) this becomes the place that talks to the ml service instead.
"""
from __future__ import annotations

from cassini_detect.pretrained_models import Pretrained


class CassiniClassifier:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self._pret = Pretrained(linear_layer=model_path)

    def prob(self, image_bytes: bytes) -> float:
        """P(cassini) for a JPEG frame."""
        return self._pret.isCassini(image_bytes)[0, 1].item()
