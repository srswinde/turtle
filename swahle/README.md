# Swahle Smart Home

Consolidated FastAPI app for the Swahle smart-home stack. Public zone (hole-cam
still gallery, no auth) + private zone (`/app/*`, behind Cloudflare Access).
See [`../smarthome/swahle-smarthome-spec.md`](../smarthome/swahle-smarthome-spec.md)
for the full architecture and migration plan.

This app is built **alongside** the legacy Tornado app (`../run.py`) and points
at the same MariaDB. Nothing in the current serving path changes until we
deliberately cut over.

## Status

- **Milestone 1 — scaffold:** app factory, module contract, settings, auth dep,
  shared DB/session, `/healthz`. ✅
- **Milestone 2 — turtle_db port:** public hole-cam gallery + read endpoints,
  private classify/resnet/train tools, Redis live websocket feed. ✅
- **Milestone 3 — dashboard + devices:** `shared/devices/` (Shelly/WLED
  registry) + dashboard module (private `/app`). ✅
- **Milestone 4 — cameras ingest:** capture worker (hole/shed) writing to the
  RAID date-dir layout + DB, plus a private live-snapshot route. ✅
- **Milestone 5 — detect/retrain:** web Retrain UI + isolated training
  subprocess (versioned checkpoints, status/logs), active-model pointer with
  one-click rollback. ✅
- **Milestone 6 — deployment:** Dockerfiles (web + ml), `docker-compose.yml`
  (web/worker-hole/worker-shed/ml/db/redis/cloudflared), tunnel config, and the
  **[DEPLOY.md](DEPLOY.md)** runbook (Cloudflare tunnel + Access + DNS cut). ✅
  *(Config + web image verified locally; the tunnel/Access/DNS steps are run by
  you against your Cloudflare account — see DEPLOY.md.)*

All v1 functionality is built. What remains is operational: running the
DEPLOY.md steps on the home server + Cloudflare, then the DNS cut.

## Layout

```
swahle/
  app/          app factory, settings, deps (auth), templating
  shared/       module contract, db engine/session, ORM models
  modules/
    turtle_db/  public.py, private.py, live.py, module.py
  frontend/
    templates/  adapted from the legacy Tornado templates
```

## Run (dev)

Run from the **repo root** (so the legacy `turtle_db` package, whose ORM models
are reused, is importable) using the anaconda python that has the DB stack:

```bash
cd /home/scott/git-clones/turtle_db
PYTHONPATH=swahle /home/scott/anaconda3/bin/python -m uvicorn app.main:app \
    --app-dir swahle --host 0.0.0.0 --port 8001 --reload
```

- Public landing: <http://localhost:8001/>
- Health: <http://localhost:8001/healthz>
- API docs: <http://localhost:8001/docs>
- Private (dev bypass on): <http://localhost:8001/app/classify>

The legacy Tornado app keeps running on port 8000; this runs on 8001.

### Ingest worker (separate process)

```bash
cd /home/scott/git-clones/turtle_db
PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python -m modules.cameras.worker --camera hole
PYTHONPATH=swahle:. /home/scott/anaconda3/bin/python -m modules.cameras.worker --camera shed
```

Safety flags for testing (write nothing to prod): `--once` (single frame),
`--dry-run` (no DB/recording/lights/notify), `--outdir DIR` (images to DIR
instead of the RAID mount), `--no-ml` (skip model loading).

### Retraining (detect module)

The web app exposes the retrain UI at `/app/detect` and drives training as an
**isolated subprocess** (`modules.detect.train`) so it never blocks the gallery.
Checkpoints are versioned under `SWAHLE_MODEL_ROOT/<timestamp>/model_<i>/model.pth`;
the active checkpoint is tracked by an `ACTIVE` pointer file with `ACTIVE.prev`
for one-click rollback. The ingest worker resolves the active checkpoint at
startup, so activate/rollback takes effect on the next worker restart. Training
fetches labeled images over HTTP from `SWAHLE_TRAIN_IMAGE_BASE` (a running web
instance's `/hole-cam/static`).

## Routes

Public (`/`):
- `GET /` — landing (latest hole-cam image, temps, week of sightings)
- `POST /api/hole-cam/latest`, `POST /api/temperatures`, `POST /api/detections`
- `GET /detections` — day gallery
- `GET /hole-cam/static/{path}` — hole-cam stills from the RAID mount

Private (`/app`, `require_user`):
- `GET /app/classify` + `GET /app/api/hole-cam/uncategorized` + `POST /app/api/hole-cam/label`
- `GET /app/resnet` + `POST /app/api/resnet`
- `GET /app/resnet/train` + `POST /app/api/resnet/train` + `POST /app/api/resnet/train/update`
- `WS /app/ws/live` — live camera/temperature feed
- `GET /app/live-static/{path}` — live-feed images from `/mnt/turtle`

## Not yet ported

- `images-playground.html` still references the legacy `/cassini/detect`
  (old turtle-imgs analysis) — that endpoint is out of this milestone's scope.
