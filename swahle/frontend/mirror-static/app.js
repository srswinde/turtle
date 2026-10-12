/* Swahle smart mirror client.
   One page, layered views. The server (SSE /mirror/events) is the source of
   truth for the takeover + tray; everything else is local view state. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const api = (url, opts) => fetch(url, Object.assign({ method: "POST" }, opts));

  // Inline SVGs for tray icons -- the Pi's Chromium has no color-emoji font, so
  // event icons (sent as emoji chars, or as names) resolve to these instead.
  const TORTOISE = '<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M10.5 6.4c-4.1 0-7 3-7 6.8 0 .9.7 1.6 1.6 1.6h10.8c.9 0 1.6-.7 1.6-1.6 0-3.8-2.9-6.8-7-6.8z"/>' +
    '<ellipse cx="18.7" cy="12.3" rx="2.6" ry="2" fill="currentColor"/>' +
    '<rect x="5.3" y="13.5" width="2.7" height="3.7" rx="1.35" fill="currentColor"/>' +
    '<rect x="13" y="13.5" width="2.7" height="3.7" rx="1.35" fill="currentColor"/>' +
    '<path fill="currentColor" d="M3.6 11.8 1.2 10.9c-.5-.2-.95.4-.6.85l1 1.5z"/></svg>';
  const DROPLET = '<svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M12 2.8c-.35 0-.68.18-.87.48C9.1 6.6 5.6 12 5.6 15.4a6.4 6.4 0 0 0 12.8 0c0-3.4-3.5-8.8-5.53-12.14A1.02 1.02 0 0 0 12 2.8z"/></svg>';
  const ICON_SVG = {
    "\u{1F422}": TORTOISE, tortoise: TORTOISE,           // hole/shed sighting
    "\u{1F4A7}": DROPLET, droplet: DROPLET, water: DROPLET, // pump
  };
  const iconMarkup = (icon) => ICON_SVG[icon] || `<span>${icon || "•"}</span>`;

  const layers = {
    ambient: $("#ambient"), takeover: $("#takeover"),
    menu: $("#menu"), view: $("#view"),
  };
  let current = "ambient";
  function show(name) {
    if (current === name) return;
    Object.entries(layers).forEach(([k, el]) => el.classList.toggle("show", k === name));
    current = name;
    if (name !== "menu") clearTimeout(menuTimer);
    if (name !== "view") clearTimeout(viewIdle);
    applyBrightness();  // a takeover at night lights up; back to ambient re-dims
  }

  /* ---------- connection ---------- */
  const dot = $("#status-dot");
  let es;
  function connect() {
    es = new EventSource("/mirror/events");
    es.onopen = () => dot.className = "ok";
    es.onerror = () => { dot.className = "bad"; };
    es.onmessage = (e) => {
      let msg; try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === "state") applyState(msg);
    };
  }
  // EventSource auto-reconnects; also hard-resync on wake / visibility.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) fetch("/mirror/state").then(r => r.json()).then(applyState).catch(() => {});
  });

  /* ---------- server state ---------- */
  let serverSkew = 0; // ms: server_time - client_now
  function applyState(st) {
    if (!st) return;
    serverSkew = (st.server_time || Date.now()) - Date.now();
    renderTray(st.icons || []);
    const a = st.active;
    if (a && a.event) {
      showTakeover(a.event);
    } else if (current === "takeover") {
      show("ambient");
    }
  }

  /* ---------- takeover ---------- */
  const SIGHTING_TYPES = ["hole_sighting", "shed_sighting"];
  let takeoverEvent = null;   // the event currently on the takeover screen

  function showTakeover(ev) {
    takeoverEvent = ev;
    $("#takeover-title").textContent = ev.title || "";
    $("#takeover-sub").textContent = ev.subtitle || "";
    $("#tk-fp").hidden = !SIGHTING_TYPES.includes(ev.type);
    const img = $("#takeover-img");
    const url = ev.image_url || "";
    img.src = url ? (url + (url.includes("?") ? "&" : "?") + "t=" + Date.now()) : "";
    if (current === "menu" || current === "view") return; // don't yank an active interaction
    show("takeover");
  }
  function dismissTakeover() {
    api("/mirror/dismiss").catch(() => {});
    show("ambient");
  }
  // tap the image (or the caption backdrop) dismisses; buttons don't
  layers.takeover.addEventListener("click", (e) => {
    if (e.target.closest("#takeover-actions")) return;
    dismissTakeover();
  });
  $("#tk-back").addEventListener("click", (e) => { e.stopPropagation(); dismissTakeover(); });
  $("#tk-fp").addEventListener("click", (e) => {
    e.stopPropagation();
    askConfirm("Are you sure you don't see Cassini?", async () => {
      const ev = takeoverEvent;
      try {
        await api("/mirror/feedback/false-positive", {
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(ev),
        });
      } catch { /* server will still be consistent via SSE */ }
      show("ambient");
    });
  });

  /* ---------- confirm modal ---------- */
  function askConfirm(text, onOk) {
    $("#confirm-text").textContent = text;
    $("#confirm-modal").hidden = false;
    $("#confirm-ok").onclick = () => { $("#confirm-modal").hidden = true; onOk(); };
    $("#confirm-cancel").onclick = () => { $("#confirm-modal").hidden = true; };
  }

  /* ---------- tray ---------- */
  const R = 44, CIRC = 2 * Math.PI * R;
  let lastIcons = [];
  function renderTray(icons) {
    lastIcons = icons;
    const tray = $("#tray");
    tray.innerHTML = "";
    for (const ic of icons) {
      const now = Date.now() + serverSkew;
      const frac = Math.max(0, Math.min(1, (ic.expires - now) / (ic.expires - ic.created)));
      const el = document.createElement("div");
      el.className = "tray-icon";
      el.innerHTML =
        `<svg class="ring-svg" viewBox="0 0 92 92"><circle class="track" cx="46" cy="46" r="${R}"></circle>` +
        `<circle class="ring" cx="46" cy="46" r="${R}" stroke-dasharray="${CIRC}" ` +
        `stroke-dashoffset="${CIRC * (1 - frac)}"></circle></svg>` +
        iconMarkup(ic.event.icon);
      let hold;
      el.addEventListener("pointerdown", () => {
        hold = setTimeout(() => { hold = null; api(`/mirror/icons/${ic.type}/clear`); }, 600);
      });
      el.addEventListener("pointerup", () => {
        if (hold) { clearTimeout(hold); api(`/mirror/icons/${ic.type}/open`); }
      });
      el.addEventListener("pointerleave", () => clearTimeout(hold));
      tray.appendChild(el);
    }
  }

  /* ---------- slideshow ---------- */
  let slides = [], si = 0, showA = true, slideCount = 0;
  let liveRefreshTimer = null;   // refreshes the live frame in place while it's up
  const LIVE_EVERY = { v: 4 };
  let liveUrl = "/mirror/img/live/hole";
  async function loadSlides() {
    try {
      const d = await (await fetch("/mirror/api/slideshow")).json();
      slides = (d.slides || []).map(s => s.url).filter(Boolean);
      LIVE_EVERY.v = d.live_every || 4;
      liveUrl = d.live_url || liveUrl;
      for (let i = slides.length - 1; i > 0; i--) { // shuffle
        const j = (Math.random() * (i + 1)) | 0; [slides[i], slides[j]] = [slides[j], slides[i]];
      }
    } catch { /* keep whatever we had */ }
  }
  function nextSlide() {
    clearInterval(liveRefreshTimer);
    $("#live-badge").hidden = true;
    if (effectiveBrightness() === 0) return;  // screen is dark -- don't decode/fetch
    slideCount++;
    const isLive = slideCount % LIVE_EVERY.v === 0;
    let url;
    if (isLive) {
      url = liveUrl + "?t=" + Date.now();
    } else if (slides.length) {
      url = slides[si % slides.length]; si++;
    } else { return; }
    const incoming = document.querySelector(showA ? ".slide[data-b]" : ".slide[data-a]");
    const outgoing = document.querySelector(showA ? ".slide[data-a]" : ".slide[data-b]");
    const pre = new Image();
    pre.onload = () => {
      incoming.style.backgroundImage = `url("${url}")`;
      incoming.classList.add("on");
      incoming.classList.toggle("kb", !isLive);   // no Ken Burns pan on the live frame
      outgoing.classList.remove("on", "kb");
      showA = !showA;
      if (isLive) {
        $("#live-badge").hidden = false;
        // keep the frame actually live: refresh it in place until the next slide
        liveRefreshTimer = setInterval(() => {
          const u = `${liveUrl}?t=${Date.now()}`;
          const im = new Image();
          im.onload = () => { incoming.style.backgroundImage = `url("${u}")`; };
          im.src = u;
        }, 3000);
      }
    };
    pre.onerror = () => {};
    pre.src = url;
  }
  setInterval(nextSlide, 15000);

  /* ---------- menu + hotspot ---------- */
  let menuTimer, viewIdle;
  $("#hotspot").addEventListener("click", () => {
    if (current === "ambient" || current === "takeover") {
      show("menu");
      menuTimer = setTimeout(() => show("ambient"), 30000);
    }
  });
  $("#menu-close").addEventListener("click", () => show("ambient"));
  document.querySelectorAll(".menu-btn[data-view]").forEach(b =>
    b.addEventListener("click", () => openView(b.dataset.view)));
  $("#view-back").addEventListener("click", () => show("ambient"));

  // Manual screen-off: tell the backend, then drop straight to ambient so
  // the panel (once display-power.sh on the Pi actually cuts the backlight)
  // comes back to the clock/slideshow rather than a leftover menu. Any touch
  // on the dark panel wakes it back on -- that's handled by display-power.sh
  // watching the touch overlay directly, not by this page.
  $("#menu-screen-off").addEventListener("click", () => {
    api("/mirror/api/display/off").catch(() => {});
    show("ambient");
  });

  function bumpViewIdle() {
    clearTimeout(viewIdle);
    viewIdle = setTimeout(() => show("ambient"), 90000);
  }
  layers.view.addEventListener("pointerdown", bumpViewIdle);

  async function openView(name) {
    document.querySelectorAll(".subview").forEach(s => s.hidden = true);
    show("view");
    bumpViewIdle();
    if (name === "cameras") return renderCameras();
    if (name === "controls") return renderControls();
    if (name === "cassini") return renderCassini(null);
    if (name === "forecast") return renderForecast();
  }

  /* ---------- cameras ---------- */
  let camTimer;
  async function renderCameras() {
    const box = $("#v-cameras"); box.hidden = false; box.innerHTML = "";
    clearInterval(camTimer);
    let list = [];
    try { list = (await (await fetch("/mirror/api/cameras")).json()).cameras || []; } catch {}
    for (const c of list) {
      const t = document.createElement("div");
      t.className = "cam-tile";
      t.innerHTML = `<img alt="${c.name}"><b>${c.name}</b>`;
      box.appendChild(t);
    }
    const refresh = () => box.querySelectorAll(".cam-tile img").forEach((img, i) => {
      img.src = list[i].url + "?t=" + Date.now();
    });
    refresh();
    camTimer = setInterval(() => { if (current === "view" && !$("#v-cameras").hidden) refresh(); }, 1000);
  }

  /* ---------- controls ---------- */
  const SWATCHES = ["#ffffff", "#ffb347", "#ff5555", "#55ff77", "#5599ff", "#c060ff"];
  const DEV_LABEL = { light: "Shed light", pump: "Water pump", shed: "Shed WLED", bookcase: "Bookcase WLED" };
  const KNOWN_DEVICES = [
    { id: "light", kind: "shelly" }, { id: "pump", kind: "shelly" },
    { id: "shed", kind: "wled" }, { id: "bookcase", kind: "wled" },
  ];
  let controlsCache = null;   // last-seen [{id,kind,state}], survives view switches
  let controlsTimer = null;

  const setJSON = (id, body) => api(`/mirror/api/controls/${id}/set`,
    { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function patchRow(row, st) {
    row.classList.toggle("off-reach", st.reachable === false);
    const tog = row.querySelector(".toggle");
    if (tog && !row.classList.contains("pending") && "on" in st) tog.classList.toggle("on", !!st.on);
    const inp = row.querySelector(".bri input");
    if (inp && document.activeElement !== inp && st.brightness != null) inp.value = st.brightness;
    const sw = row.querySelector(".swatches");
    if (sw && st.color) sw.querySelectorAll(".swatch").forEach(x =>
      x.classList.toggle("sel", x.dataset.color.toLowerCase() === String(st.color).toLowerCase()));
  }

  function applyDeviceState(id, st) {
    if (!st) return;
    const row = $(`#v-controls .ctl[data-id="${id}"]`);
    if (row) patchRow(row, st);
    const d = controlsCache && controlsCache.find(x => x.id === id);
    if (d) d.state = { ...d.state, ...st };
  }

  function ctlRow(d) {
    const row = document.createElement("div");
    row.className = "ctl"; row.dataset.id = d.id;
    row.innerHTML = `<div class="name">${DEV_LABEL[d.id] || d.id}<small>${d.kind}</small></div>`;

    const tog = document.createElement("button");
    tog.className = "toggle";
    tog.onclick = async () => {
      const want = !tog.classList.contains("on");
      tog.classList.toggle("on", want);              // optimistic -- no re-render
      row.classList.add("pending");
      try {
        const r = await (await api(`/mirror/api/controls/${d.id}/toggle`)).json();
        row.classList.remove("pending");
        applyDeviceState(d.id, r);                   // reconcile just this row
      } catch {
        row.classList.remove("pending");
        tog.classList.toggle("on", !want);          // revert
      }
    };
    row.appendChild(tog);

    if (d.kind === "wled") {
      const sw = document.createElement("div"); sw.className = "swatches";
      SWATCHES.forEach(c => {
        const s = document.createElement("button");
        s.className = "swatch"; s.style.background = c; s.dataset.color = c;
        s.onclick = async () => {
          sw.querySelectorAll(".swatch").forEach(x => x.classList.toggle("sel", x === s));
          try { applyDeviceState(d.id, await (await setJSON(d.id, { color: c })).json()); } catch {}
        };
        sw.appendChild(s);
      });
      row.appendChild(sw);

      const bri = document.createElement("label"); bri.className = "bri";
      bri.innerHTML = `<input type="range" min="0" max="255">`;
      bri.querySelector("input").onchange = async (e) => {
        try { applyDeviceState(d.id, await (await setJSON(d.id, { brightness: +e.target.value })).json()); } catch {}
      };
      row.appendChild(bri);
    }
    patchRow(row, d.state || {});
    return row;
  }

  function fillControls(devices) {
    const box = $("#v-controls");
    const have = [...box.querySelectorAll(".ctl")].map(r => r.dataset.id).join(",");
    const want = devices.map(d => d.id).join(",");
    if (have !== want) {                 // first paint / structure changed -> build
      box.innerHTML = "";
      devices.forEach(d => box.appendChild(ctlRow(d)));
    } else {                             // same rows -> patch state in place, no rebuild
      devices.forEach(d => { const r = box.querySelector(`.ctl[data-id="${d.id}"]`); if (r) patchRow(r, d.state || {}); });
    }
  }

  async function renderControls() {
    const box = $("#v-controls"); box.hidden = false;
    clearInterval(controlsTimer);
    box.classList.toggle("loading", !controlsCache);
    fillControls(controlsCache || KNOWN_DEVICES.map(d => ({ ...d, state: {} })));   // instant paint
    const load = async () => {
      try {
        const devices = (await (await fetch("/mirror/api/controls")).json()).devices || [];
        controlsCache = devices;
        box.classList.remove("loading");
        if (current === "view" && !box.hidden) fillControls(devices);
      } catch {}
    };
    load();
    controlsTimer = setInterval(() => {
      if (current === "view" && !box.hidden) load(); else clearInterval(controlsTimer);
    }, 12000);
  }

  /* ---------- cassini's day ---------- */
  let cassiniDate = null;
  async function renderCassini(dateStr) {
    const view = $("#v-cassini"); view.hidden = false;
    let d;
    try {
      const q = dateStr ? "?date=" + dateStr : "";
      d = await (await fetch("/mirror/api/cassini" + q)).json();
    } catch { return; }
    cassiniDate = d.date;
    $("#cassini-date").textContent = d.is_today ? "Today" : d.date;
    const hole = $("#cassini-hole"), shed = $("#cassini-shed");
    hole.innerHTML = ""; shed.innerHTML = "";
    if (!d.hole.length) hole.innerHTML = `<p class="empty">No hole-cam visits.</p>`;
    for (const s of d.hole) {
      const el = document.createElement("div");
      el.className = "entry";
      const kf = (s.keyframes && (s.keyframes.peak || s.keyframes.first)) || "";
      el.innerHTML = (kf ? `<img src="${kf}" alt="">` : "") +
        `<div class="meta">${clock(s.start)} – ${clock(s.end)}` +
        `<small>${Math.round(s.duration_s / 60)} min` +
        (s.away_before_s ? ` · away ${Math.round(s.away_before_s / 60)} min before` : "") + `</small></div>`;
      hole.appendChild(el);
    }
    if (!d.shed.length) shed.innerHTML = `<p class="empty">No shed-cam detections.</p>`;
    for (const s of d.shed) {
      const el = document.createElement("div");
      el.className = "entry";
      el.innerHTML = (s.image_url ? `<img src="${s.image_url}" alt="">` : "") +
        `<div class="meta">${clock(s.ts)}<small>score ${(s.score || 0).toFixed(2)}</small></div>`;
      shed.appendChild(el);
    }
  }
  document.querySelectorAll(".cassini-head [data-day]").forEach(b =>
    b.addEventListener("click", () => {
      const base = cassiniDate ? new Date(cassiniDate + "T12:00:00") : new Date();
      base.setDate(base.getDate() + (+b.dataset.day));
      renderCassini(base.toISOString().slice(0, 10));
    }));

  /* ---------- 5-day forecast ---------- */
  let forecastCache = null;   // last-seen days, survives view switches
  let forecastSelected = 0;   // index into forecastCache -- which tile's text shows below
  function forecastDetail(d) {
    if (!d) return "";
    return (d.detail ? `<div class="fc-detail-section"><h4>${d.name}</h4><p>${d.detail}</p></div>` : "") +
      (d.night_detail && d.night_detail !== d.detail
        ? `<div class="fc-detail-section"><h4>${d.night_name || "Night"}</h4><p>${d.night_detail}</p></div>` : "");
  }
  function forecastRows(days) {
    if (!days.length) return `<p class="empty">Forecast unavailable.</p>`;
    const tiles = days.map((d, i) => `
      <div class="fc-day${i === forecastSelected ? " selected" : ""}" data-i="${i}">
        <div class="fc-name">${d.name}</div>
        <div class="fc-icon">${d.icon || "•"}</div>
        <div class="fc-short">${d.short || ""}</div>
        <div class="fc-temps">
          <span class="hi">${d.high_f != null ? d.high_f + "°" : "--"}</span>
          <span class="lo">${d.low_f != null ? d.low_f + "°" : "--"}</span>
        </div>
        ${d.precip_pct != null ? `<div class="fc-precip">💧 ${d.precip_pct}%</div>` : ""}
      </div>`).join("");
    return `<div class="fc-row">${tiles}</div><div id="fc-detail">${forecastDetail(days[forecastSelected])}</div>`;
  }
  function paintForecast(days) {
    const box = $("#v-forecast");
    box.innerHTML = forecastRows(days);
    box.querySelectorAll(".fc-day").forEach(el => el.addEventListener("click", () => {
      forecastSelected = +el.dataset.i;
      paintForecast(days);
    }));
  }
  async function renderForecast() {
    const box = $("#v-forecast"); box.hidden = false;
    forecastSelected = 0;   // today, every time the view is (re)opened
    if (forecastCache) paintForecast(forecastCache);   // instant paint
    let d;
    try { d = await (await fetch("/mirror/api/forecast")).json(); } catch { return; }
    forecastCache = d.days || [];
    if (current === "view" && !box.hidden) paintForecast(forecastCache);
  }

  /* ---------- ambient info + quiet hours ---------- */
  let quietCfg = null;      // {active, dim, takeover_dim, wake_seconds}
  let wokeUntil = 0;        // ms: touch during quiet hours un-dims until this time
  let ambientTimer = null;

  function effectiveBrightness() {
    if (!quietCfg || !quietCfg.active) return 1;
    if (Date.now() < wokeUntil) return 1;
    if (current === "menu" || current === "view") return 1;  // mid-interaction
    if (current === "takeover") return quietCfg.takeover_dim;
    return quietCfg.dim;
  }
  function applyBrightness() {
    const b = effectiveBrightness();
    document.body.style.filter = b < 1 ? `brightness(${b})` : "";
    if (Date.now() < wokeUntil) setTimeout(applyBrightness, wokeUntil - Date.now() + 50);
  }
  // a touch anywhere during quiet hours wakes the screen briefly
  document.addEventListener("pointerdown", () => {
    if (quietCfg && quietCfg.active && Date.now() >= wokeUntil) {
      wokeUntil = Date.now() + (quietCfg.wake_seconds || 30) * 1000;
      applyBrightness();
    }
  }, true);

  function setChip(sel, html, stale) {
    const el = $(sel);
    if (!html) { el.hidden = true; return; }
    el.innerHTML = html;
    el.hidden = false;
    el.classList.toggle("stale", !!stale);
  }
  function relSince(ms) {
    const m = Math.max(0, (Date.now() - ms) / 60000);
    if (m < 60) return Math.round(m) + "m";
    const h = m / 60;
    if (h < 24) return (h < 10 ? h.toFixed(1) : Math.round(h)) + "h";
    return Math.round(h / 24) + "d";
  }
  async function loadAmbient() {
    let d;
    try { d = await (await fetch("/mirror/api/ambient")).json(); }
    catch { ambientTimer = setTimeout(loadAmbient, 60000); return; }

    quietCfg = d.quiet || null;

    const w = d.weather;
    setChip("#chip-weather", w &&
      `${w.icon} ${w.temp_f}° <span class="lo">↑${w.high_f} ↓${w.low_f}</span>`);

    const s = d.solar || {};
    const parts = [];
    if (s.pv_w != null) parts.push(`☀️ ${s.pv_w} W`);
    if (s.soc != null) parts.push(`🔋 ${s.soc}%`);
    else if (s.batt_v != null) parts.push(`🔋 ${s.batt_v} V`);
    setChip("#chip-solar", parts.length ? parts.join(' <span class="lo">·</span> ') : null, s.stale);

    const c = d.cassini || {};
    let ct = null;
    if (c.home) ct = "🐢 home";
    else if (c.last_seen) ct = `🐢 away <span class="lo">${relSince(c.last_seen)}</span>`;
    setChip("#chip-cassini", ct);

    applyBrightness();
    ambientTimer = setTimeout(loadAmbient, (d.poll_seconds || 60) * 1000);
  }

  /* ---------- helpers ---------- */
  function clock(ms) {
    return new Date(ms).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }
  function tickClock() {
    const now = new Date();
    $("#time").textContent = now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    $("#date").textContent = now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  }
  setInterval(tickClock, 10000); tickClock();

  // re-render tray rings smoothly between server pushes
  setInterval(() => { if (lastIcons.length) renderTray(lastIcons); }, 5000);

  /* ---------- go ---------- */
  loadSlides().then(() => { nextSlide(); setInterval(loadSlides, 3600000); });
  connect();
  loadAmbient();
  fetch("/mirror/state").then(r => r.json()).then(applyState).catch(() => {});
})();
