/* Swahle session guard: on private (/app) pages, poll Cloudflare Access session
   validity every 20 min; on expiry, blur the page and prompt to re-authenticate. */
(function () {
  if (!location.pathname.startsWith('/app')) return;   // private pages only

  var CHECK_MS = 20 * 60 * 1000;
  var OVERLAY_ID = 'swahle-reauth-overlay';

  async function authed() {
    try {
      var r = await fetch('/app/api/whoami', {
        method: 'GET', redirect: 'manual', cache: 'no-store',
        headers: { 'X-Requested-With': 'fetch' },
      });
      if (r.type === 'opaqueredirect' || r.status === 0) return false; // bounced to CF login
      if (r.status === 200) return true;
      if (r.status === 401 || r.status === 403) return false;
      return r.ok;
    } catch (e) { return false; }
  }

  function showReauth() {
    if (document.getElementById(OVERLAY_ID)) return;
    document.body.style.filter = 'blur(6px)';
    document.body.style.pointerEvents = 'none';
    var ov = document.createElement('div');
    ov.id = OVERLAY_ID;
    ov.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:2147483647', 'display:flex',
      'align-items:center', 'justify-content:center', 'background:rgba(6,10,15,.55)',
      'font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif',
    ].join(';');
    ov.innerHTML =
      '<div style="background:#171d25;color:#e6eaf0;border:1px solid #2a333f;border-radius:16px;' +
      'padding:1.8rem 1.9rem;max-width:360px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.55)">' +
        '<div style="font-size:2.2rem;line-height:1">🔒</div>' +
        '<h2 style="margin:.5rem 0 .35rem;font-size:1.25rem">Session expired</h2>' +
        '<p style="margin:0 0 1.2rem;color:#93a0b4;font-size:.92rem;line-height:1.45">' +
          'Your secure session has timed out. Please re-authenticate to continue.</p>' +
        '<button id="swahle-reauth-btn" style="cursor:pointer;background:#3fae6b;color:#06110a;' +
        'border:none;border-radius:9px;padding:.65rem 1.3rem;font-weight:700;font-size:.95rem">' +
        'Re-authenticate</button>' +
      '</div>';
    // Append to <html> so the overlay itself is NOT blurred by the body filter.
    document.documentElement.appendChild(ov);
    document.getElementById('swahle-reauth-btn').addEventListener('click', function () {
      location.reload();   // expired -> Cloudflare bounces to login -> returns here
    });
  }

  function clearReauth() {
    var ov = document.getElementById(OVERLAY_ID);
    if (ov) ov.remove();
    document.body.style.filter = '';
    document.body.style.pointerEvents = '';
  }

  async function check() { (await authed()) ? clearReauth() : showReauth(); }

  setTimeout(check, 3000);                 // shortly after load
  setInterval(check, CHECK_MS);            // every 20 min
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') check();   // and when the tab refocuses
  });
})();
