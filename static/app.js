function isIos() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

function formatPrice(value) {
  return Number(value).toFixed(2);
}

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

async function parseApiResponse(response) {
  const raw = await response.text();
  if (!raw) {
    return { data: {}, raw };
  }
  try {
    return { data: JSON.parse(raw), raw };
  } catch {
    return { data: null, raw };
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    window.location.href = '/';
    throw new Error('Unauthorized');
  }
  return response;
}

function renderAlerts(alerts) {
  const container = document.getElementById('alerts-list');
  if (!alerts.length) {
    container.innerHTML = '<p class="empty-state">No alerts yet.</p>';
    return;
  }

  container.innerHTML = alerts
    .map((alert) => {
      const sideLabel = alert.side === 'buy' ? 'Buy' : 'Sell';
      return `
        <article class="alert-item">
          <div class="alert-meta">
            <div>
              <strong>${alert.ticker}</strong>
              <div class="muted">${sideLabel} ${alert.share_count.toLocaleString()} @ $${formatPrice(alert.target_price)}</div>
            </div>
            <span class="status ${alert.status}">${alert.status}</span>
          </div>
          <div class="muted">Last checked: ${formatDate(alert.last_checked_at)}</div>
          <div class="muted">Triggered: ${formatDate(alert.triggered_at)}</div>
          <div class="alert-actions">
            <button type="button" data-delete-id="${alert.id}">Delete</button>
          </div>
        </article>
      `;
    })
    .join('');
}

async function loadAlerts() {
  const response = await api('/api/alerts');
  const alerts = await response.json();
  renderAlerts(alerts);
}

let ibkrPollTimer = null;

function formatApiError(data, raw, fallback) {
  if (data) {
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((item) => item.msg || JSON.stringify(item)).join('; ');
    }
    if (data.detail && typeof data.detail === 'object') {
      return data.detail.message || data.detail.error || JSON.stringify(data.detail);
    }
    return data.message || data.error || fallback;
  }
  if (raw) {
    const trimmed = raw.trim();
    if (trimmed.startsWith('<')) return fallback;
    return trimmed.slice(0, 300);
  }
  return fallback;
}

function setIbkrError(message) {
  const errorEl = document.getElementById('ibkr-error');
  if (!errorEl) return;
  if (message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  } else {
    errorEl.textContent = '';
    errorEl.hidden = true;
  }
}

function appendIbkrLog(lines) {
  const logEl = document.getElementById('ibkr-log');
  if (!logEl || !lines?.length) return;

  const timestamp = new Date().toLocaleTimeString();
  const block = lines.map((line) => `[${timestamp}] ${line}`).join('\n');
  logEl.hidden = false;
  logEl.textContent = logEl.textContent ? `${logEl.textContent}\n${block}` : block;
  logEl.scrollTop = logEl.scrollHeight;
}

function openIbkrVncModal() {
  const modal = document.getElementById('ibkr-vnc-modal');
  const frame = document.getElementById('ibkr-vnc-frame');
  if (!modal || !frame || modal.open) return;
  frame.src = '/ibkr/vnc';
  modal.showModal();
}

function closeIbkrVncModal() {
  const modal = document.getElementById('ibkr-vnc-modal');
  const frame = document.getElementById('ibkr-vnc-frame');
  if (!modal || !modal.open) return;
  modal.close();
  if (frame) {
    frame.src = 'about:blank';
  }
}

function maybeOpenIbkrVncModal(data) {
  if (data?.vnc_available && data.status === 'connecting') {
    openIbkrVncModal();
  }
}

function updateIbkrUi(data) {
  const statusEl = document.getElementById('ibkr-status');
  const loginBtn = document.getElementById('ibkr-login-btn');
  if (!statusEl || !loginBtn) return;

  statusEl.textContent = data.message || 'Unknown status';
  statusEl.className = `status ${data.status || 'disconnected'}`;

  if (data.steps?.length) {
    appendIbkrLog(data.steps);
  }

  if (data.error) {
    setIbkrError(data.error);
  } else if (data.status !== 'error') {
    setIbkrError('');
  }

  if (data.status === 'connected') {
    closeIbkrVncModal();
    loginBtn.hidden = true;
    return;
  }

  loginBtn.hidden = false;
  loginBtn.disabled = data.status === 'connecting';
  loginBtn.textContent = data.status === 'connecting' ? 'Connecting...' : 'Connect IBKR';

  maybeOpenIbkrVncModal(data);
}

function stopIbkrPolling() {
  if (ibkrPollTimer !== null) {
    clearInterval(ibkrPollTimer);
    ibkrPollTimer = null;
  }
}

function startIbkrPolling() {
  stopIbkrPolling();
  const startedAt = Date.now();
  appendIbkrLog(['Polling for IBKR connection status...']);
  ibkrPollTimer = setInterval(async () => {
    if (Date.now() - startedAt > 120000) {
      stopIbkrPolling();
      const statusEl = document.getElementById('ibkr-status');
      if (statusEl) {
        statusEl.textContent = 'Connection timed out. Try Connect IBKR again and approve 2FA on your phone.';
        statusEl.className = 'status error';
      }
      setIbkrError('No connection after 2 minutes. Check gateway logs: docker logs stock-alert-ib-gateway');
      const loginBtn = document.getElementById('ibkr-login-btn');
      if (loginBtn) {
        loginBtn.hidden = false;
        loginBtn.disabled = false;
        loginBtn.textContent = 'Connect IBKR';
      }
      return;
    }

    try {
      const response = await api('/api/ibkr/status');
      const { data, raw } = await parseApiResponse(response);
      if (!response.ok || !data) {
        throw new Error(formatApiError(data, raw, 'Could not read IBKR status.'));
      }
      updateIbkrUi(data);
      if (data.status === 'connected' || data.status === 'error') {
        stopIbkrPolling();
      }
    } catch (error) {
      console.error(error);
      appendIbkrLog([`Status poll failed: ${error.message}`]);
    }
  }, 3000);
}

async function loadIbkrStatus() {
  const response = await api('/api/ibkr/status');
  const { data, raw } = await parseApiResponse(response);
  if (!response.ok || !data) {
    throw new Error(formatApiError(data, raw, 'Could not load IBKR status.'));
  }
  updateIbkrUi(data);
  if (data.status === 'connecting') {
    maybeOpenIbkrVncModal(data);
    startIbkrPolling();
  }
}

async function connectIbkr() {
  const loginBtn = document.getElementById('ibkr-login-btn');
  const statusEl = document.getElementById('ibkr-status');
  setIbkrError('');
  appendIbkrLog(['Button clicked. Sending login request to server...']);

  if (loginBtn) {
    loginBtn.disabled = true;
    loginBtn.textContent = 'Connecting...';
  }
  if (statusEl) {
    statusEl.textContent = 'Starting IB Gateway...';
    statusEl.className = 'status connecting';
  }

  let response;
  let data = null;
  let raw = '';
  try {
    response = await api('/api/ibkr/login', { method: 'POST' });
    ({ data, raw } = await parseApiResponse(response));
  } catch (error) {
    const message = error.message || 'Network error while contacting server.';
    appendIbkrLog([`Request failed: ${message}`]);
    setIbkrError(message);
    if (statusEl) {
      statusEl.textContent = 'Could not contact server.';
      statusEl.className = 'status error';
    }
    if (loginBtn) {
      loginBtn.disabled = false;
      loginBtn.textContent = 'Connect IBKR';
    }
    return;
  }

  if (!response.ok || !data) {
    const detail = data?.detail;
    const steps = detail?.steps || data?.steps || [];
    const errorMessage = formatApiError(data, raw, 'Could not start IBKR login.');
    if (steps.length) {
      appendIbkrLog(steps);
    } else {
      appendIbkrLog([errorMessage]);
    }
    setIbkrError(detail?.error || data.error || errorMessage);
    if (statusEl) {
      statusEl.textContent = detail?.message || errorMessage;
      statusEl.className = 'status error';
    }
    if (loginBtn) {
      loginBtn.disabled = false;
      loginBtn.textContent = 'Connect IBKR';
    }
    return;
  }

  updateIbkrUi(data);
  if (data.status !== 'connected') {
    startIbkrPolling();
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

async function enablePushNotifications() {
  const statusEl = document.getElementById('push-status');
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    statusEl.textContent = 'Push notifications are not supported in this browser.';
    return;
  }

  if (isIos() && !isStandalone()) {
    statusEl.textContent = 'On iPhone, add this app to your Home Screen first, then enable notifications.';
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    statusEl.textContent = 'Notification permission was not granted.';
    return;
  }

  const registration = await navigator.serviceWorker.register('/sw.js');
  await navigator.serviceWorker.ready;

  const configResponse = await api('/api/config');
  const config = await configResponse.json();
  if (!config.vapid_public_key) {
    statusEl.textContent = 'Server VAPID key is not configured.';
    return;
  }

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(config.vapid_public_key),
  });

  await api('/api/push/subscribe', {
    method: 'POST',
    body: JSON.stringify(subscription),
  });

  statusEl.textContent = 'Notifications enabled for this device.';
}

function setupIosBanner() {
  const banner = document.getElementById('ios-banner');
  if (!banner) return;
  if (isIos() && !isStandalone()) {
    banner.hidden = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setupIosBanner();
  loadAlerts().catch(console.error);
  loadIbkrStatus().catch((error) => {
    console.error(error);
    appendIbkrLog([`Initial status check failed: ${error.message}`]);
    setIbkrError(error.message);
    const statusEl = document.getElementById('ibkr-status');
    if (statusEl) {
      statusEl.textContent = 'Could not load IBKR status.';
      statusEl.className = 'status error';
    }
  });

  const ibkrLoginBtn = document.getElementById('ibkr-login-btn');
  if (ibkrLoginBtn) {
    ibkrLoginBtn.addEventListener('click', () => {
      connectIbkr().catch((error) => {
        appendIbkrLog([`Unexpected error: ${error.message}`]);
        setIbkrError(error.message);
        const statusEl = document.getElementById('ibkr-status');
        if (statusEl) {
          statusEl.textContent = 'Unexpected error while connecting.';
          statusEl.className = 'status error';
        }
        ibkrLoginBtn.disabled = false;
        ibkrLoginBtn.textContent = 'Connect IBKR';
      });
    });
  }

  const ibkrVncCloseBtn = document.getElementById('ibkr-vnc-close');
  const ibkrVncModal = document.getElementById('ibkr-vnc-modal');
  if (ibkrVncCloseBtn) {
    ibkrVncCloseBtn.addEventListener('click', () => {
      closeIbkrVncModal();
    });
  }
  if (ibkrVncModal) {
    ibkrVncModal.addEventListener('cancel', (event) => {
      event.preventDefault();
      closeIbkrVncModal();
    });
  }

  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', async () => {
      await api('/api/logout', { method: 'POST' });
      window.location.href = '/';
    });
  }

  const alertForm = document.getElementById('alert-form');
  if (alertForm) {
    alertForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const errorEl = document.getElementById('alert-form-error');
      errorEl.hidden = true;

      const payload = {
        ticker: alertForm.ticker.value.trim().toUpperCase(),
        side: alertForm.side.value,
        share_count: Number(alertForm.share_count.value),
        target_price: Number(alertForm.target_price.value),
      };

      const response = await api('/api/alerts', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        errorEl.textContent = data.detail?.[0]?.msg || data.detail || 'Could not create alert.';
        errorEl.hidden = false;
        return;
      }

      alertForm.reset();
      await loadAlerts();
    });
  }

  const alertsList = document.getElementById('alerts-list');
  if (alertsList) {
    alertsList.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-delete-id]');
      if (!button) return;
      const alertId = button.getAttribute('data-delete-id');
      const errorEl = document.getElementById('alerts-error');
      if (errorEl) errorEl.hidden = true;

      const response = await api(`/api/alerts/${alertId}`, { method: 'DELETE' });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        if (errorEl) {
          errorEl.textContent = data.detail || 'Could not delete alert.';
          errorEl.hidden = false;
        }
        return;
      }

      await loadAlerts();
    });
  }

  const enablePushBtn = document.getElementById('enable-push-btn');
  if (enablePushBtn) {
    enablePushBtn.addEventListener('click', () => {
      enablePushNotifications().catch((error) => {
        document.getElementById('push-status').textContent = error.message;
      });
    });
  }

  const testPushBtn = document.getElementById('test-push-btn');
  if (testPushBtn) {
    testPushBtn.addEventListener('click', async () => {
      const statusEl = document.getElementById('push-status');
      try {
        const response = await api('/api/push/test', { method: 'POST' });
        const data = await response.json();
        statusEl.textContent = data.sent > 0
          ? `Test notification sent to ${data.sent} device(s).`
          : 'No devices are subscribed yet. Tap "Enable notifications" first.';
      } catch (error) {
        statusEl.textContent = error.message;
      }
    });
  }
});
