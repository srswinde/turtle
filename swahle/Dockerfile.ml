# SUPERSEDED — not built/run in the live deployment. The GPU services (hole-cam
# and shed-cam ingest, the ml/OWLv2 training service) actually run as host
# systemd units against a plain anaconda Python env with direct GPU access —
# see DEPLOY.md "GPU services (host systemd)" and scripts/ml-host-requirements.txt.
# This file is kept as the original containerized design in case it's revisited
# (e.g. a second/non-anaconda host). Do not `docker compose up worker-hole
# worker-shed ml` alongside the systemd units — duplicate GPU processes will
# fight over the same DB.
#
# ML image: used by the ingest workers (in-process inference) and the `ml`
# training service. CUDA base; needs the NVIDIA Container Toolkit on the host
# (verify `nvidia-smi` + DKMS first — see DEPLOY.md §GPU). For a CPU-only start,
# swap the base for python:3.11-slim and install CPU torch.
FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Web deps (no torch — torch/torchvision come from the base image) + the extras
# cassini_detect imports at module load.
COPY swahle/requirements.txt /app/swahle/requirements.txt
RUN pip install --no-cache-dir -r /app/swahle/requirements.txt matplotlib tqdm transformers

# cassini_detect (vendored from the sibling repo — see scripts/prepare-build.sh).
COPY swahle/vendor/cassini_detect/ /app/cassini_detect/
RUN pip install --no-cache-dir /app/cassini_detect

COPY swahle/ /app/swahle/
COPY turtle_db/ /app/turtle_db/

ENV PYTHONPATH=/app/swahle:/app

# Default command runs the ml training service; the worker services override it.
CMD ["uvicorn", "modules.detect.ml_service:app", "--host", "0.0.0.0", "--port", "9200"]
