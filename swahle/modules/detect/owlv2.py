"""Open-vocabulary tortoise localization (OWLv2) for the shed-cam acquisition step.

Unlike the Faster R-CNN detector (fixed COCO classes, no tortoise class), OWLv2
matches image regions against arbitrary text prompts in a shared embedding
space, so it can be asked for "a tortoise" directly. Runs on the same GPU host
as the retrain service; imports torch, so this only ever loads inside `ml_service`.
"""
from __future__ import annotations

import io
import logging
from functools import lru_cache

import torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor

log = logging.getLogger("swahle.detect.owlv2")

MODEL_ID = "google/owlv2-base-patch16-ensemble"
DEFAULT_PROMPTS = ["a photo of a tortoise", "a tortoise"]
DEFAULT_THRESHOLD = 0.15  # OWLv2 scores run lower than closed-set detectors; tune empirically


@lru_cache(maxsize=1)
def _load():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor = Owlv2Processor.from_pretrained(MODEL_ID)
    model = Owlv2ForObjectDetection.from_pretrained(MODEL_ID).eval().to(device)
    log.info("OWLv2 loaded on %s", device)
    return processor, model, device


def locate(image_bytes: bytes, prompts: list[str] | None = None,
          threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Return boxes for the given text prompts, sorted by score descending."""
    prompts = prompts or DEFAULT_PROMPTS
    processor, model, device = _load()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    inputs = processor(text=[prompts], images=img, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)

    target_sizes = torch.tensor([img.size[::-1]], device=device)  # (h, w)
    results = processor.post_process_grounded_object_detection(
        outputs=outputs, target_sizes=target_sizes, threshold=threshold,
    )[0]

    hits = [
        {
            "prompt": prompts[label_idx],
            "score": float(score),
            "box": [float(x) for x in box],  # [x1, y1, x2, y2] in pixel coords
        }
        for score, label_idx, box in zip(
            results["scores"].tolist(), results["labels"].tolist(), results["boxes"].tolist()
        )
    ]
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits
