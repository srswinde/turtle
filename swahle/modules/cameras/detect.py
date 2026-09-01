"""torchvision Faster R-CNN detector for extra object info (ported verbatim).

Imports torch, so this is only loaded by the ingest worker, never by the web app.
"""
from __future__ import annotations

import io

import torch
from PIL import Image
from torchvision import transforms
from torchvision.models.detection import (
    FasterRCNN_ResNet50_FPN_Weights,
    fasterrcnn_resnet50_fpn,
)

_transform = transforms.Compose([
    transforms.Resize((300, 300)),
    transforms.ToTensor(),
])


class Detector:
    def __init__(self) -> None:
        self.device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        weights = FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        self.model = fasterrcnn_resnet50_fpn(weights=weights).eval().to(self.device)
        self._categories = weights.meta["categories"]

    def get_class_name(self, num: int) -> str:
        return self._categories[num]

    def eval(self, image_bytes: bytes, prob_threshold: float = 0.75) -> list[dict]:
        img = Image.open(io.BytesIO(image_bytes))
        tensor = _transform(img).to(self.device)
        with torch.no_grad():
            result = self.model(tensor.unsqueeze(0))
        return self._clean(result, prob_threshold)

    def _clean(self, result, prob_threshold: float) -> list[dict]:
        detections = []
        for idx, score in enumerate(result[0]["scores"]):
            prob = score.item()
            if prob > prob_threshold:
                class_number = result[0]["labels"][idx].item()
                detections.append(dict(
                    prob=prob,
                    class_number=class_number,
                    class_name=self.get_class_name(class_number),
                    x1=result[0]["boxes"][idx][0].item(),
                    y1=result[0]["boxes"][idx][1].item(),
                    x2=result[0]["boxes"][idx][2].item(),
                    y2=result[0]["boxes"][idx][3].item(),
                ))
        return detections
