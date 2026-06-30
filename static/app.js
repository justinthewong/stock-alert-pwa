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
      await api(`/api/alerts/${alertId}`, { method: 'DELETE' });
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
