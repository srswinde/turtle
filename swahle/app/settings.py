"""Environment-driven configuration for the Swahle app.

Everything is overridable via ``SWAHLE_*`` environment variables (or a .env file),
so the same image runs in dev and in Docker Compose without code changes.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# swahle/  (repo/swahle) — used to locate bundled frontend assets by default.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SWAHLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -----------------------------------------------------------
    # Keep MariaDB (the existing datadir); only the driver string is wired here.
    database_uri: str = "mysql+pymysql://scott:scott@localhost/turtle"

    # --- Image storage ------------------------------------------------------
    # Preserve the existing date-based directory structure on the RAID array.
    hole_cam_dir: Path = Path("/mnt/nfs/hole-cam")
    shed_cam_dir: Path = Path("/mnt/nfs/shed-cam")
    turtle_img_dir: Path = Path("/mnt/turtle/imgs")
    # Root the live-feed image paths (/mnt/turtle/...) are served from.
    live_img_root: Path = Path("/mnt/turtle")

    # --- Device registry (Shelly relays + WLED strips) ----------------------
    # Env-overridable as JSON (SWAHLE_SHELLY_DEVICES / SWAHLE_WLED_DEVICES).
    shelly_devices: dict[str, dict] = {
        "light": {"endpoint": "http://192.168.1.4/rpc", "switch_id": 0},
        "pump": {"endpoint": "http://192.168.1.252/rpc", "switch_id": 0},
    }
    wled_devices: dict[str, str] = {
        "shed": "192.168.1.5",
        "bookcase": "192.168.0.86",
    }

    # --- Dashboard polling cadence -----------------------------------------
    hole_poll_seconds: float = 60.0
    shed_poll_seconds: float = 30.0
    shed_history_hours: int = 12

    # --- Cameras / ingest ---------------------------------------------------
    # Amcrest PoE cameras. Credentials default to the current values for dev;
    # override via SWAHLE_CAMERAS (JSON) in production / .env.
    cameras: dict[str, dict] = {
        "hole": {
            "ip": "192.168.0.36", "port": "80",
            "username": "admin", "password": "4_sw@hle_4",
            "basedir": "/mnt/nfs/hole-cam",
            "latest_path": "/mnt/nfs/hole-cam/latest.jpg",
            "rtsp_port": "554", "channel": "1", "subtype": "0",
        },
        "shed": {
            "ip": "192.168.0.27", "port": "80",
            "username": "admin", "password": "admin",
            "basedir": "/mnt/nfs/shed-cam",
            "latest_path": "/mnt/nfs/shed-cam/latest.jpg",
            "rtsp_port": "554", "channel": "1", "subtype": "0",
            "light_ip": "192.168.1.159",
        },
    }
    # Cassini (VGG) classifier checkpoint used by the hole-cam ingest.
    cassini_model_path: str = "/mnt/nfs/models/20260617-0833/model_499/model.pth"

    # --- Live video (2026-08-23) --------------------------------------------
    # MediaMTX pulls each camera's RTSP sub-stream continuously and remuxes to
    # HLS (audio included -- both cameras' RTSP streams carry AAC, confirmed
    # via ffprobe). The web app reverse-proxies /app/live/<cam>/... to this so
    # the browser never talks to MediaMTX directly -- same Cloudflare Access
    # auth as every other private route, no new tunnel/Access config. Reached
    # via host.docker.internal since MediaMTX runs as a standalone `docker run`
    # container on the same plain bridge network as swahle-web itself (see
    # swahle-web-deploy memory -- NOT part of `docker compose up`, same
    # dup-service trap applies). Config: mediamtx/mediamtx.yml.
    # Detection frame-sampling (hole_cam.py/shed_cam.py) intentionally stays
    # on the separate Amcrest snapshot() API, not this stream -- see
    # naturalist memory on why that pipeline's input source is left alone.
    mediamtx_url: str = ""

    # --- MediaMTX for the shed yard search only (v2, narrowed 2026-09-11) ---
    # v1 also routed ShedScanner's idle poll through MediaMTX -- that added a
    # *permanent* RTSP connection to the shed cam on top of
    # swahle-shed-cam.service's existing snapshot.cgi polling, and rebooted
    # the camera within 40 minutes (see naturalist/acquisition.py ShedScanner
    # ._tick docstring). Idle now unconditionally reads whatever
    # swahle-shed-cam.service already captured off disk -- zero extra camera
    # load, this setting doesn't touch it.
    #
    # This setting only affects the yard search (scanning/tracking, i.e.
    # after a resting-view hit): "camera" = legacy snapshot.cgi per grid
    # step/track poll (the pre-2026-09 behavior); "mediamtx" = pull the same
    # moment's frame from mediamtx_rtsp_url via ffmpeg instead -- a
    # *replacement* for that snapshot.cgi call, not an addition, since PTZ
    # still moves the camera directly either way (RTSP can't carry ONVIF).
    # Empty mediamtx_rtsp_url -> "mediamtx" mode fails closed (grab errors,
    # logged, no frame that step) rather than silently falling back.
    mediamtx_rtsp_url: str = ""
    frame_grab_timeout: float = 18.0           # > sourceOnDemandStartTimeout (15s) for a cold shed path
    shed_scan_frame_source: str = "camera"
    mediamtx_scan_path: str = "shed_hi"        # mediamtx.yml path used for detection (main stream)

    # --- Pump alert (gentle red pulse on the shed WLED while the pump runs) --
    pump_alert_enabled: bool = True
    pump_alert_pump: str = "pump"       # relay device id to watch
    pump_alert_light: str = "shed"      # wled device id to pulse
    pump_alert_color: str = "#ff0000"   # red
    pump_alert_fx: int = 2              # WLED effect (2 = Breathe / gentle pulse)
    pump_alert_speed: int = 40          # WLED sx; lower = slower
    pump_alert_poll_seconds: float = 8.0
    # Shelly -> this app webhook plumbing (LAN):
    hook_base_url: str = "http://192.168.0.164:8001"   # what the Shelly calls back
    hook_token: str = "swahle-pump-hook"               # shared secret in the hook URL

    # --- Naturalist / perception (LLM day analysis) ------------------------
    # Hosted Claude (vision) analyzes the frames around each departure to say
    # which way Cassini left. Low volume, so a hosted call is cheap + best.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    departure_frames: int = 5           # frames to send Claude per departure
    departure_pre_seconds: float = 15.0  # window before the last high-prob frame
    departure_post_seconds: float = 12.0  # window after (Cassini exiting / gone)
    departure_max_px: int = 512         # downscale longest edge before upload
    # Directions that do NOT lead toward the shed-cam (comma-separated), i.e. the
    # complement of "toward shed". Only retreating into the hole/burrow keeps him
    # out of shed-cam range -- any other exit direction puts him somewhere in the
    # yard the shed-cam can potentially see (corrected 2026-07-14: originally an
    # allowlist of just "down-right", which was backwards -- see naturalist memory).
    shed_unreachable_directions: str = "away-from-camera"
    # Findings from the OWLv2 hole/shed correlation investigation (scripts/owlv2_*.py),
    # written by the offline analysis scripts, read live by the /app/naturalist/owlv2 page.
    naturalist_data_dir: Path = Path("/mnt/nfs/naturalist")

    # --- Pose estimation (facing direction, DeepLabCut) ---------------------
    # Path to the trained DLC project's config.yaml (see scripts/pose_export_dlc.py
    # + pose_train.py). Empty until a model has actually been trained --
    # modules.detect.pose.estimate() no-ops (all-None keypoints) until set.
    pose_model_config: str = ""

    # --- Naturalist Phase 2: shed-cam scanning ------------------------------
    naturalist_auto_analyze: bool = True        # auto-run Claude exit-direction on every departure
    naturalist_poll_seconds: float = 60.0       # how often the watcher checks for new departures
    naturalist_departure_ready_seconds: float = 200.0  # safety margin before treating a session as "ended"
    naturalist_lookback_seconds: float = 86400.0  # how far back the watcher scans for unanalyzed sessions
    # Continuous, unconditional scan (2026-07-16: no longer gated on a
    # toward-shed hole-cam departure -- that single LLM classification being
    # wrong meant real shed-cam sightings were silently missed).
    shed_scan_poll_seconds: float = 20.0        # how often to check the latest shed-cam frame
    # OWLv2 score to call it a confirmed sighting. Raised 0.2 -> 0.45 on
    # 2026-08-09: sampled real hits showed 0.2-0.4 was mostly noise (a person
    # silhouette scored 0.20; a garbage "PTZ booting up" glitch frame scored
    # 0.42), while >=0.6 was consistently a clean, real tortoise photo. 0.45
    # keeps some margin into the genuinely mixed 0.4-0.5 band rather than
    # cutting it entirely. See naturalist memory for the investigation.
    shed_scan_hit_threshold: float = 0.45
    shed_scan_prompts: list[str] = ["a tortoise", "a turtle shell"]
    shed_scan_hit_cooldown_seconds: float = 300.0  # min gap between logged hits (avoid spam while he lingers)
    shed_scan_daylight_start_hour: int = 8      # Arizona local time; matches the OWLv2 spot-mining window
    shed_scan_daylight_end_hour: int = 21

    # --- Naturalist Phase 3: hole-cam session verification (OWLv2 2nd opinion) --
    # 2026-08-23: the VGG hole-cam classifier isn't finely calibrated -- real
    # sightings and some false positives (lens debris, shadow/light flicker on
    # the empty dark hole interior) score in the same ~0.70-0.74 band, so no
    # prob threshold can separate them (see sessionize.MIN_SESSION_FRAMES
    # docstring for the persistence-gate fix that catches lens-debris blips but
    # not the flicker pattern). OWLv2 is a differently-trained, localized
    # (bounding-box) detector, so it doesn't share this failure mode. Phase 1
    # rollout: verify each session once in the background and cache the
    # verdict, but only *flag* it in the UI -- don't filter/hide anything yet,
    # since there's no calibration data for OWLv2 on this close-up, often-IR
    # hole-cam domain (unlike shed-cam, where a real sample picked 0.45). Once
    # a threshold is validated against real flagged sessions, this can start
    # gating sessionize.py's output directly.
    hole_scan_verify_enabled: bool = True
    hole_scan_verify_poll_seconds: float = 60.0
    hole_scan_verify_threshold: float = 0.1     # deliberately low/permissive for Phase 1 -- see above
    hole_scan_prompts: list[str] = ["a tortoise", "a turtle shell"]

    # --- Naturalist Phase 2.5: shed-cam pan/tilt yard scan ------------------
    # 2026-07-27: reverses the old "no PTZ steering" decision -- once OWLv2
    # confirms Cassini at the shed cam's resting view, actively sweep the
    # yard to relocate him instead of only watching the static frame. Grid
    # is the same azimuth {0,90,180,270} x tilt {0,45} pattern the original
    # shed_cam capture loop used (recovered from census_shed_cam.csv
    # filenames -- no live copy of that script exists anymore).
    shed_scan_pantilt_enabled: bool = True
    # Resting/home position (queried live via ptz_status() 2026-07-29, after
    # Scott repointed the camera to the correct spot). Used as the canonical
    # position to return to after a yard search, rather than trusting
    # whatever the camera happened to be sitting at when the hit fired.
    shed_scan_home_pan: float = 248.81
    shed_scan_home_tilt: float = 53.34
    shed_scan_pantilt_grid: list[list[float]] = [
        [0, 0], [0, 45], [90, 0], [90, 45],
        [180, 0], [180, 45], [270, 0], [270, 45],
    ]
    shed_scan_pantilt_track_poll_seconds: float = 15.0  # re-check interval while parked on a found position
    shed_scan_pantilt_home_confirm_seconds: float = 90.0  # hole-cam presence within this window means "he's home"
    # Safety net only -- give up and return to the resting position after this
    # long if the hole cam never confirms he's home (so a missed/failed
    # confirmation doesn't strand the camera off its resting view indefinitely).
    shed_scan_pantilt_max_search_seconds: float = 14400.0

    # --- Managed standalone services (systemd system units) ----------------
    # Web control panel starts/stops these via the host services agent.
    managed_services: dict[str, dict] = {
        "hole-cam": {"unit": "swahle-hole-cam.service",
                     "description": "Hole camera capture + detection loop"},
        "shed-cam": {"unit": "swahle-shed-cam.service",
                     "description": "Shed camera capture + detection loop"},
        "ml": {"unit": "swahle-ml.service",
               "description": "Cassini retrain / ML service (GPU)"},
    }
    # Web -> host services agent (set in the container, like the ml service).
    service_agent_url: str = ""

    # --- Detect / retraining (ml) ------------------------------------------
    # Versioned checkpoints live under model_root/<timestamp>/model_<i>/model.pth.
    model_root: Path = Path("/mnt/nfs/models")
    # Where the training subprocess fetches labeled hole-cam images from
    # (a running web instance serving /hole-cam/static).
    train_image_base: str = "http://127.0.0.1:8001/hole-cam/static"
    train_default_finetunes: int = 10
    train_default_samples: int = 100
    train_default_since_days: int = 90
    # When set (Docker), the web app delegates training to the isolated ml
    # service instead of spawning a local subprocess (dev). e.g. http://ml:9200
    ml_service_url: str = ""
    # IFTTT notification for a confirmed shed-cam OWLv2 sighting (see
    # modules/naturalist/acquisition.py:ShedScanner). Disabled unless
    # notify_enabled is true. notify_wait_seconds throttles re-notification
    # while he's still lingering at the shed (separate from, and longer than,
    # shed_scan_hit_cooldown_seconds which only throttles DB logging).
    notify_enabled: bool = False
    notify_ifttt_url: str = ""
    notify_wait_seconds: int = 3600
    # Base URL the notification's link points at (Cassini's Day / shed detections page).
    notify_site_base_url: str = "https://new.swahle.org"

    # --- Aravaipa Canyon permit availability watcher (public zone) ---------
    # Polls recreation.gov's own JSON API (no auth needed -- the same endpoint
    # their React app calls) every aravaipa_poll_seconds and records only
    # CHANGES in quota (so the event log reads as "what happened when", not a
    # firehose of identical polls). Page is in the public zone but gated by a
    # simple shared-password cookie (aravaipa_password) -- deliberately NOT
    # Cloudflare Access / require_user, per Scott's request. Poller is disabled
    # (no-op) until aravaipa_password is set.
    aravaipa_permit_id: str = "251864"          # Aravaipa Canyon Wilderness Permits
    aravaipa_password: str = ""                 # required to view /aravaipa; blank = disabled
    aravaipa_cookie_secret: str = ""            # HMAC key for the auth cookie; falls back to the password if unset
    aravaipa_poll_seconds: float = 120.0        # 2 minutes, per Scott's request
    # Explicit "YYYY-MM" list -- scoped to the season Scott actually cares about
    # rather than polling everything N months forward. Update this each year.
    aravaipa_months: list[str] = ["2026-09", "2026-10"]

    # --- Live feed ----------------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    # Redis pub/sub channels the ingest side publishes to.
    redis_channels: list[str] = [
        "turtle_conditions",
        "home_image",
        "backyard_image",
        "tanspot_image",
    ]

    # --- Smart mirror (separate LAN-only process: app.mirror_app) ----------
    # Runs as its own uvicorn/systemd unit on mirror_port, bound to
    # mirror_bind_host (this box's LAN IP). Deliberately NOT routed through the
    # Cloudflare tunnel -- network isolation is the gate. mirror_token, if set,
    # is a secondary check: the mirror enrolls once via /mirror/enroll?token=...
    # which drops a signed cookie. Events cross from swahle-web over Redis
    # (mirror_event_channel); see shared/events.py.
    mirror_bind_host: str = "192.168.0.164"
    mirror_port: int = 8800
    mirror_token: str = ""                 # blank = no cookie gate (LAN-only already)
    mirror_event_channel: str = "mirror:events"
    mirror_takeover_ttl: int = 1800        # seconds a takeover screen holds (30 min)
    mirror_icon_ttl_hours: int = 8         # tray icon lifetime before auto-clear
    mirror_slide_pool_size: int = 150      # top-N shed detections in the slideshow pool
    mirror_slide_window_days: int = 60     # how far back to draw slideshow candidates
    mirror_slide_person_threshold: float = 0.2   # OWLv2 "person" score that rejects a slide
    mirror_slide_rebuild_seconds: int = 10800    # pool re-vet cadence (3h)
    mirror_slide_live_every: int = 4       # every Nth slide is a fresh live hole-cam frame
    mirror_reopen_ttl: int = 120          # takeover length when a tray icon is tapped open
    mirror_tick_seconds: int = 10         # state reconcile / expiry cadence
    # Live hole-cam presence -> mirror takeover (runs in swahle-web alongside the
    # naturalist pollers; emits over the Redis bus, harmless if no mirror is up).
    mirror_producers_enabled: bool = True
    mirror_hole_poll_seconds: float = 30.0
    mirror_hole_cooldown_seconds: int = 900       # one takeover per visit, not a stream
    mirror_hole_fresh_seconds: int = 300          # a frame older than this doesn't count as "now"
    mirror_static_dir: Path = PACKAGE_ROOT / "frontend" / "mirror-static"
    # Local (not NFS -- see swahle-nfs-boot-race): the systemd unit sets
    # StateDirectory=swahle so /var/lib/swahle exists and is owned by the user.
    mirror_state_path: Path = Path("/var/lib/swahle/mirror_state.json")
    # --- Ambient info panel + quiet hours (mirror /mirror/api/ambient) -----
    mirror_ambient_poll_seconds: int = 60
    mirror_cassini_home_seconds: int = 600     # last hi-prob hole frame within this -> "home"
    # open-meteo (no API key). Midtown Tucson; override via SWAHLE_MIRROR_WEATHER_LAT/LON.
    mirror_weather_lat: float = 32.22
    mirror_weather_lon: float = -110.95
    # Quiet hours: each is "HH:MM" | "sunrise" | "sunset" (Arizona local time).
    # Default: 22:00 until sunrise. Takeovers still fire, at takeover_dim.
    mirror_quiet_start: str = "22:00"
    mirror_quiet_end: str = "sunrise"
    mirror_quiet_dim: float = 0.0              # ambient-screen brightness multiplier when quiet (0 = black)
    mirror_quiet_takeover_dim: float = 0.5     # ... for an event takeover when quiet
    mirror_quiet_wake_seconds: int = 30        # a screen touch un-dims for this long

    # --- Frontend -----------------------------------------------------------
    templates_dir: Path = PACKAGE_ROOT / "frontend" / "templates"
    static_dir: Path = PACKAGE_ROOT / "frontend" / "static"
    dashboard_assets_dir: Path = PACKAGE_ROOT / "frontend" / "dashboard"

    # --- Auth (Cloudflare Access) ------------------------------------------
    # Cloudflare Access is the primary gate; the app verifies the injected JWT
    # as defense-in-depth. Left blank in dev, where require_user degrades to a
    # dev identity instead of rejecting requests (see app/deps.py).
    cf_access_team_domain: str = ""   # e.g. "swahle.cloudflareaccess.com"
    cf_access_aud: str = ""           # Access application AUD tag
    dev_bypass_auth: bool = True      # allow /app/* without a CF token in dev

    # --- Memorable subdomains -> page served at that host's root -----------
    # e.g. {"power.swahle.org": "controls.html", "camera.swahle.org": "cameras.html"}
    # Set via SWAHLE_SITE_PAGES (JSON). The subdomain is gated by its own Access
    # app; its AUD goes in cf_access_aud (comma-separated).
    site_pages: dict[str, str] = {}

    # --- Server -------------------------------------------------------------
    temp_map: dict[str, str] = {
        "282abb75d0013cb1": "ambient",
        "289e0975d0013ca6": "underground",
    }

    # --- Solar (Victron SmartSolar MPPT "Instant Readout" over BLE) --------
    # The Shelly BLU gateway can't decrypt Victron's payload itself; it just
    # relays the raw advertisement to /hooks/solar (same LAN webhook pattern
    # as /hooks/buttons), where this app does the AES-CTR decrypt. aes_key is
    # the per-device key from VictronConnect (Settings > Product Info >
    # Instant Readout > share/show encryption key).
    solar_devices: dict[str, dict] = {
        "mppt": {
            "mac": "e7:18:75:27:44:31",
            "aes_key": "384b25dc8265e55fb9a4b0e2f6e7ddc4",
            "name": "SmartSolar MPPT 100/50",
            "kind": "solar_charger",
        },
        "shunt": {
            "mac": "e7:33:17:5e:27:12",
            "aes_key": "ecf2379267440c8626f3604b2c0064a7",
            "name": "SmartShunt HQ2510RNE7H",
            "kind": "battery_monitor",
        },
    }
    solar_log_path: Path = Path("/mnt/nfs/solar/mppt.jsonl")


settings = Settings()
