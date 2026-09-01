# Swahle Deployment Runbook

Deploys the whole stack via Docker Compose behind a Cloudflare Tunnel + Access.
Follows the spec's **add-alongside-then-switch** approach: stand everything up
and test it on a **test hostname** while the old DigitalOcean/nginx path keeps
serving `swahle.org`, then cut DNS only when it works.

```
Internet → Cloudflare (DNS + TLS + Access/Google OAuth)
             └─ Cloudflare Tunnel (outbound from home; no port forwarding)
                   └─ home server (docker compose): web · worker-hole · worker-shed · ml · db · redis · cloudflared
                        └─ bind mounts: /mnt/nfs (images + models), /mnt/turtle
```

> **GPU services now run via host systemd, not Docker.** The `worker-hole`,
> `worker-shed`, and `ml` services below (and `Dockerfile.ml`) were the original
> plan and are kept for reference, but in the actual live deployment the
> GPU-side services (hole-cam ingest, shed-cam ingest, the `ml` training/OWLv2
> service) run as host systemd units against a plain anaconda Python env with
> direct GPU access — no NVIDIA Container Toolkit involved. See
> **"GPU services (host systemd) — what's actually running"** below instead of
> the `docker compose ... worker-hole worker-shed ml` instructions in §2 and §6.
> `web`/`db`/`redis`/`cloudflared` may also differ from this doc in the current
> live deployment — verify with `docker ps` / `systemctl` before trusting any
> single command here.

All commands run from the repo root: `/home/scott/git-clones/turtle_db`.

---

## 0. Prerequisites

- Docker + Compose v2 on the home server (verified: 26.1.3 / v2.27.0).
- RAID mounts present on the host: `/mnt/nfs` (hole-cam, shed-cam, models) and,
  for the live feed, `/mnt/turtle`.
- A **free Cloudflare account** with the `swahle.org` zone added (nameservers
  pointed at Cloudflare). This is the one slow step — DNS propagation — and is
  required before the tunnel/Access work. You can do §1–§4 without it.

### GPU readiness (spec §8) — original Docker plan, not the live path

The `ml` and `worker-*` **compose services** use the CUDA image and would need:

```bash
nvidia-smi                      # driver healthy (verified: RTX 4070, 580.x)
# NVIDIA Container Toolkit installed + configured for Docker:
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

If the GPU isn't ready, comment out the `deploy.resources` blocks in
`swahle/docker-compose.yml` and switch `Dockerfile.ml` to a CPU torch base —
ship CPU-first, enable GPU last.

**This path is not what's actually deployed** — see the next section.

### GPU services (host systemd) — what's actually running

The GPU-side services run directly on the host against the shared **anaconda**
Python (`/home/scott/anaconda3/bin/python`), which already has CUDA-enabled
torch installed and sees the GPU natively — no container GPU passthrough, no
NVIDIA Container Toolkit. Confirm GPU health the same simple way:

```bash
nvidia-smi                      # driver healthy (verified: RTX 4070, 580.x)
/home/scott/anaconda3/bin/python -c "import torch; print(torch.cuda.is_available())"
```

Three systemd units, defined in `/etc/systemd/system/`:

| Unit | Runs | Port |
|---|---|---|
| `swahle-hole-cam.service` | `python -m modules.cameras.worker --camera hole` | — |
| `swahle-shed-cam.service` | `python -m modules.cameras.worker --camera shed` | — |
| `swahle-ml.service` | `uvicorn modules.detect.ml_service:app` (training + OWLv2 `/detect/locate`) | 9200 |

Each unit's `WorkingDirectory` is the repo root with
`PYTHONPATH=swahle:<repo-root>`, so **code changes take effect on the next
`systemctl restart` — no image build, no `git pull` into a container**:

```bash
systemctl status swahle-ml.service swahle-hole-cam.service swahle-shed-cam.service
sudo systemctl restart swahle-ml.service      # after code changes under modules/detect/
journalctl -u swahle-ml.service -f            # logs
```

Dependencies for this env are **not** installed via `swahle/requirements.txt`
(that file is for the web image). They're pinned for reference in
`swahle/scripts/ml-host-requirements.txt` — not auto-installed by anything;
regenerate it after intentionally changing the env. New packages go in with
`/home/scott/anaconda3/bin/pip install <pkg>` directly, then update that file.

The web app (wherever it runs — Docker or otherwise) reaches these over plain
HTTP via `SWAHLE_ML_SERVICE_URL=http://host.docker.internal:9200` (or the
Services page, which drives them through `swahle-services-agent.service`).

---

## 1. Build prep (one-time)

```bash
# Vendor the sibling cassini_detect package into the build context.
bash swahle/scripts/prepare-build.sh

# Create the deployment env file from the template and fill it in.
cp swahle/.env.example swahle/.env
$EDITOR swahle/.env         # DB_* are prefilled; CF_* / TUNNEL_ID come later
```

---

## 2. Bring up the data + app tier

The `db` service reuses the **existing** MariaDB datadir (`../data`), so stop the
old root compose first (they must not run against the same datadir):

```bash
docker compose -f docker-compose.yml down        # old mariadb+adminer stack
docker compose -f swahle/docker-compose.yml --env-file swahle/.env build web ml
docker compose -f swahle/docker-compose.yml --env-file swahle/.env up -d db redis web
```

Verify:

```bash
docker compose -f swahle/docker-compose.yml ps
curl -f http://localhost:8001/healthz            # web is published on host :8001
curl -s http://localhost:8001/ | head            # public hole-cam gallery
```

Then start the GPU services — **via host systemd, not Docker** (see "GPU
services (host systemd)" above for why):

```bash
sudo systemctl enable --now swahle-hole-cam.service swahle-shed-cam.service swahle-ml.service
journalctl -u swahle-hole-cam.service -f   # watch it capture
```

Confirm new images land in the existing RAID date-dir layout
(`/mnt/nfs/hole-cam/YYYY/M/D/…`) and rows appear in the DB before trusting it.

---

## 3. Create the Cloudflare Tunnel

On the host (installs a cert + creates a named tunnel):

```bash
cloudflared tunnel login                         # opens browser; pick swahle.org
cloudflared tunnel create swahle                 # prints a TUNNEL_ID + writes <ID>.json
```

Wire it into this repo:

```bash
cp ~/.cloudflared/<TUNNEL_ID>.json swahle/cloudflared/     # credentials (gitignored)
sed -i 's/<TUNNEL_ID>/<TUNNEL_ID>/g' swahle/cloudflared/config.yml   # replace placeholder
echo "TUNNEL_ID=<TUNNEL_ID>" >> swahle/.env                # (or edit .env)
```

Route a **test hostname** first (old path still serves swahle.org):

```bash
cloudflared tunnel route dns swahle new.swahle.org
```

Add the test hostname to `swahle/cloudflared/config.yml` ingress (there's a
commented `new.swahle.org` block ready), then start the tunnel:

```bash
docker compose -f swahle/docker-compose.yml --env-file swahle/.env up -d cloudflared
docker compose -f swahle/docker-compose.yml logs -f cloudflared
```

Visit `https://new.swahle.org/` — the public gallery should load.

---

## 4. Cloudflare Access (Google + family allowlist)

In the Cloudflare **Zero Trust** dashboard:

1. **Settings → Custom Pages / team domain**: note your team domain
   (`<team>.cloudflareaccess.com`) → this is `CF_ACCESS_TEAM_DOMAIN`.
2. **Access → Applications → Add → Self-hosted**:
   - Application domain: `swahle.org` **path** `app` (protects `/app*` only;
     leaves `/` public).
   - Identity provider: Google (add it under **Settings → Authentication** first).
   - Policy: **Allow** · Emails → your + family addresses.
3. Open the application's **Overview** → copy the **Application Audience (AUD)**
   tag → this is `CF_ACCESS_AUD`.

Put both in `swahle/.env`, then recreate `web` so it enforces the JWT
(defense-in-depth; `SWAHLE_DEV_BYPASS_AUTH` is already `false` in compose):

```bash
# swahle/.env:
#   CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
#   CF_ACCESS_AUD=<aud tag>
docker compose -f swahle/docker-compose.yml --env-file swahle/.env up -d web
```

Test on `new.swahle.org`:
- `/` → loads, no login.
- `/app/dashboard` → Google login, only allowlisted emails get in.
- Confirm **movies / shed-cam / live views are unreachable without auth**.

---

## 5. Cut over + retire the droplet

Once `new.swahle.org` is fully verified:

```bash
cloudflared tunnel route dns swahle swahle.org   # point the real hostname at the tunnel
```

- Ensure the Access application covers `swahle.org/app*` (step 4).
- Remove the `new.swahle.org` block from `config.yml` if you like; restart cloudflared.
- Watch traffic move to the tunnel, then **shut down the DigitalOcean droplet /
  nginx**. Dynamic IP + DDNS no longer matter (the tunnel dials out).

Rollback at any point: repoint DNS back to the droplet — it never changed.

---

## 6. Operations

- **Logs (web/db/redis/cloudflared, whichever run in Docker):**
  `docker compose -f swahle/docker-compose.yml logs -f <service>` — but verify
  what's actually running with `docker ps` first; see the callout at the top.
- **Logs (GPU services — hole-cam, shed-cam, ml/OWLv2):**
  `journalctl -u swahle-ml.service -f` (swap the unit name).
- **Retrain:** open `https://swahle.org/app/detect` → set finetunes/samples →
  Retrain. Runs on the host via `swahle-ml.service`, not a container;
  checkpoints land under `/mnt/nfs/models/<timestamp>/`.
- **Rollback a model:** on the same page, activate a previous checkpoint or
  "Roll back to previous". This flips the `ACTIVE` pointer.
- **Apply a model swap to ingest:** the workers read the active checkpoint at
  startup, so after activate/rollback:
  `sudo systemctl restart swahle-hole-cam.service`.
- **Update GPU-service code after changes:** `git pull` (or edit in place),
  then `sudo systemctl restart swahle-ml.service swahle-hole-cam.service
  swahle-shed-cam.service` as needed — no build step, they run straight from
  the repo.
- **Update web after code changes:**
  `bash swahle/scripts/prepare-build.sh && docker compose -f swahle/docker-compose.yml build && docker compose -f swahle/docker-compose.yml up -d`
  (again, confirm this matches how `web` is actually deployed before running it).

---

## 7. Smart mirror (LAN-only, separate process)

The smart mirror runs its own FastAPI app (`app.mirror_app:app`) as a **host
systemd unit**, bound to this box's LAN IP (`192.168.0.164:8800`). It is
**deliberately not** routed through the Cloudflare tunnel — network isolation
is the gate. Events reach it from `swahle-web` over Redis pub/sub
(`shared/events.py`, channel `mirror:events`); the producers (shed/hole-cam
sighting, pump on/off) live in the web process.

```bash
sudo cp swahle/deploy/services/swahle-mirror.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now swahle-mirror.service
curl -s http://192.168.0.164:8800/healthz          # {"status":"ok"}
```

Requirements from the host:

- **Redis** reachable at `SWAHLE_REDIS_HOST` (unit default `127.0.0.1:6379`).
  `swahle-web` must publish to the *same* Redis. If Redis only runs as a
  container, publish its `6379` to the host or point the unit at wherever it
  listens. The app degrades gracefully (reconnect loop) but no events reach the
  mirror until the bus connects.
- **MariaDB** on `localhost` (settings default, same as `swahle-ml.service`).
- **ml service** on `127.0.0.1:9200` (`swahle-ml.service`) for the slideshow's
  OWLv2 person filter.
- `StateDirectory=swahle` in the unit creates `/var/lib/swahle` for
  `mirror_state.json` (the 8-hour icon tray, survives restarts).

Optional device gate: set `SWAHLE_MIRROR_TOKEN` in the unit and enroll the Pi
once at `http://192.168.0.164:8800/mirror/enroll?token=…`.

**Update after code changes:** `git pull` then
`sudo systemctl restart swahle-mirror.service` (no build step — runs from the
repo, like the GPU units).

The **Raspberry Pi kiosk** side (Chromium in kiosk mode, autostarted in the
Pi's existing Wayfire desktop session) is set up separately — see
`swahle/deploy/kiosk/README.md`.

## Notes / deviations from the spec

- **GPU services (hole-cam ingest, shed-cam ingest, `ml`/OWLv2) run via host
  systemd against a shared anaconda Python env, not Docker/Compose.** No NVIDIA
  Container Toolkit is installed or needed — torch sees the GPU directly. This
  is the actual live deployment; `Dockerfile.ml` and the `worker-hole` /
  `worker-shed` / `ml` compose services above are the original plan, kept for
  reference but unused. Env pinned at `swahle/scripts/ml-host-requirements.txt`.
  See "GPU services (host systemd)" in §0 for the systemd unit table and commands.
- **MariaDB, not MySQL 8** — reuses the existing datadir with zero migration.
- **Training bridge:** the web app is torch-free; it delegates the training
  *launch* to the `ml` service (`SWAHLE_ML_SERVICE_URL`), while status/logs/
  checkpoints/rollback are read directly off the shared `/mnt/nfs/models` volume.
