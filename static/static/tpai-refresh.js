/* tpai-refresh.js: Persistent session refresh for OWUI SPA */
(function () {
  if (typeof window === 'undefined') return;
  if (window.__tpaiRefreshTimer) return; // Guard against duplicates

  var CSRF_COOKIE = 'tpai_csrf';
  var REFRESH_INTERVAL_MS = 13 * 60 * 1000; // 13 minutes
  var CSRF_WAIT_INTERVAL_MS = 5000; // Poll for CSRF cookie every 5s after auth

  function getCookie(name) {
    var m = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
    return m ? m[1] : null;
  }

  function syncOwuiTokenToLocalStorage() {
    try {
      var m = document.cookie.match(/(?:^|;\s*)token=([^;]+)/);
      if (m && m[1]) {
        localStorage.setItem('token', m[1]);
      }
    } catch (_) {
      // ignore
    }
  }

  function currentReturnUrl() {
    try {
      // Preserve path + query; hash is client-only and not needed serverside
      var p = window.location.pathname || '/';
      var q = window.location.search || '';
      return p + q;
    } catch (_) {
      return '/';
    }
  }

  function doRefresh() {
    var csrf = getCookie(CSRF_COOKIE);
    if (!csrf) return; // No CSRF yet — likely right after bridge redirect

    fetch('/auth/refresh', {
      method: 'POST',
      headers: {
        'X-CSRF-Token': csrf,
        'Content-Type': 'application/json'
      },
      credentials: 'same-origin',
      body: '{}'
    })
      .then(function (r) {
        if (r.ok) {
          // Server updated cookies; mirror OWUI token into localStorage
          syncOwuiTokenToLocalStorage();
        } else if (r.status === 401 || r.status === 403) {
          // Refresh token expired or revoked — force re-auth preserving location
          var ret = encodeURIComponent(currentReturnUrl());
          window.location.replace('/auth/start?return_url=' + ret);
        }
      })
      .catch(function () {
        // Network error — ignore; next interval will retry
      });
  }

  function startRefreshTimer() {
    if (window.__tpaiRefreshTimer) return;
    // Kick once shortly after load to extend session promptly
    setTimeout(doRefresh, 5000);
    window.__tpaiRefreshTimer = setInterval(doRefresh, REFRESH_INTERVAL_MS);
  }

  // If CSRF cookie exists, start timer immediately; otherwise, poll until present
  if (getCookie(CSRF_COOKIE)) {
    startRefreshTimer();
  } else {
    // Poll for CSRF cookie that appears after auth redirect/bridge
    var waitId = setInterval(function () {
      if (getCookie(CSRF_COOKIE)) {
        clearInterval(waitId);
        startRefreshTimer();
      }
    }, CSRF_WAIT_INTERVAL_MS);
  }

  // Keep localStorage token roughly in sync on navigation events
  try {
    window.addEventListener('visibilitychange', function () {
      if (!document.hidden) syncOwuiTokenToLocalStorage();
    });
    window.addEventListener('focus', syncOwuiTokenToLocalStorage);
  } catch (_) {
    // ignore
  }
})();

