(() => {
  const root = document.querySelector('[data-control-centre]');
  if (!root) return;
  const grid = document.getElementById('session-grid');
  const empty = document.getElementById('session-empty');
  const count = document.getElementById('device-count');
  const liveCount = document.getElementById('device-live-count');
  const warningCount = document.getElementById('device-warning-count');
  const basketTotal = document.getElementById('basket-total');
  const eventFilter = document.getElementById('event-filter');
  const statusFilter = document.getElementById('status-filter');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let allSessions = [];

  const seenMs = value => Date.now() - new Date(String(value).replace(' ', 'T') + 'Z').getTime();
  const isLive = s => seenMs(s.last_seen) < 20000;
  const needsAttention = s => !isLive(s) || (s.battery_level !== null && Number(s.battery_level) <= 20);
  function relativeTime(value) {
    const seconds = Math.max(0, Math.round(seenMs(value) / 1000));
    if (seconds < 10) return 'now';
    if (seconds < 60) return `${seconds}s ago`;
    return `${Math.floor(seconds / 60)}m ago`;
  }
  function uploadLabel(s) {
    const current = Number(s.upload_current || 0), total = Number(s.upload_total || 0);
    return total > 0 ? `${current} of ${total}` : 'Not reporting';
  }
  function uploadPercent(s) {
    const current = Number(s.upload_current || 0), total = Number(s.upload_total || 0);
    return total > 0 ? Math.min(100, Math.round(current / total * 100)) : 0;
  }
  function batteryLabel(s) {
    if (s.battery_level === null || s.battery_level === undefined) return 'Not reporting';
    return `${Number(s.battery_level)}%${s.is_charging ? ' · Charging' : ''}`;
  }

  async function post(token, endpoint, body) {
    const response = await fetch(`/api/control/sessions/${encodeURIComponent(token)}/${endpoint}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, cache: 'no-store', body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error('The change could not be sent.');
  }
  const command = (token, action, message=null) => post(token, 'command', {command: action, message});
  function actionButton(label, action, className='button button-small button-secondary') {
    return `<button type="button" class="${className}" data-command="${action}">${label}</button>`;
  }

  function populateEvents(sessions) {
    const previous = eventFilter.value;
    const events = [...new Set(sessions.map(s => s.event_slug).filter(Boolean))].sort();
    eventFilter.innerHTML = '<option value="">All active events</option>' + events.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join('');
    if (events.includes(previous)) eventFilter.value = previous;
  }
  function filteredSessions() {
    return allSessions.filter(s => {
      if (eventFilter.value && s.event_slug !== eventFilter.value) return false;
      if (statusFilter.value === 'live' && !isLive(s)) return false;
      if (statusFilter.value === 'attention' && !needsAttention(s)) return false;
      if (statusFilter.value === 'locked' && !s.locked) return false;
      return true;
    });
  }

  function render() {
    const sessions = filteredSessions();
    count.textContent = String(allSessions.length);
    liveCount.textContent = String(allSessions.filter(isLive).length);
    warningCount.textContent = String(allSessions.filter(needsAttention).length);
    basketTotal.textContent = String(allSessions.reduce((sum, s) => sum + Number(s.basket_count || 0), 0));
    empty.hidden = sessions.length > 0;
    empty.textContent = allSessions.length ? 'No devices match the selected filters.' : 'No iPads or customer phones have checked in during the last 10 minutes.';
    grid.innerHTML = sessions.map(s => {
      const live = isLive(s), attention = needsAttention(s);
      const stateClass = live ? (attention ? 'device-warning' : 'device-online') : 'device-stale';
      const percent = uploadPercent(s);
      return `<article class="session-card ipad-session-card ${stateClass}" data-token="${esc(s.session_token)}">
        <div class="session-card-heading"><div><span class="device-dot" aria-hidden="true"></span><div><h2>${esc(s.device_name)}</h2><small>${esc(s.operator_name || 'Operator unassigned')}${s.ballroom ? ` · ${esc(s.ballroom)}` : ''}</small></div></div><span class="session-state">${live ? (attention ? 'Attention' : 'Live') : 'Reconnecting'}</span></div>
        <dl class="session-facts ipad-session-facts">
          <div><dt>Event</dt><dd>${esc(s.event_slug || 'Not selected')}</dd></div>
          <div><dt>Current page</dt><dd>${esc(s.page)}</dd></div>
          <div><dt>Gallery</dt><dd>${esc(s.current_gallery || 'Not reporting')}</dd></div>
          <div><dt>Status</dt><dd>${esc(s.locked ? 'Locked for assistance' : s.status)}</dd></div>
          <div><dt>Basket</dt><dd>${Number(s.basket_count || 0)} item${Number(s.basket_count || 0) === 1 ? '' : 's'}</dd></div>
          <div><dt>Battery</dt><dd>${batteryLabel(s)}</dd></div>
          <div><dt>Connection</dt><dd>${esc(s.network_quality || s.connection_type || 'Not reporting')}</dd></div>
          <div><dt>Last seen</dt><dd>${relativeTime(s.last_seen)}</dd></div>
        </dl>
        <div class="ipad-upload"><div><span>Upload progress</span><strong>${uploadLabel(s)}</strong></div><div class="progress-track"><span style="width:${percent}%"></span></div></div>
        <details class="ipad-assignment"><summary>Device assignment</summary><div class="assignment-fields"><label>Operator<input type="text" maxlength="80" value="${esc(s.operator_name || '')}" data-operator></label><label>Ballroom<input type="text" maxlength="80" value="${esc(s.ballroom || '')}" data-ballroom></label><button type="button" class="button button-small" data-save-assignment>Save assignment</button></div></details>
        <div class="session-actions">
          <button type="button" class="button button-small button-secondary" data-rename-device>Rename iPad</button>
          ${actionButton('Welcome', 'SHOW_WELCOME', 'button button-small')}
          ${actionButton('Message', 'SHOW_MESSAGE')}
          ${actionButton('Reload', 'RELOAD')}
          ${s.locked ? actionButton('Unlock', 'UNLOCK') : actionButton('Lock', 'LOCK')}
          ${actionButton('Clear basket', 'CLEAR_BASKET')}
          ${actionButton('End session', 'END_SESSION', 'button button-small button-danger')}
        </div>
      </article>`;
    }).join('');

    grid.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', async () => {
      const card = button.closest('[data-token]'), token = card.dataset.token, action = button.dataset.command;
      let message = null;
      if (action === 'SHOW_MESSAGE') { message = prompt('Message to display on this iPad:', 'A member of staff is coming to help you.'); if (!message) return; }
      if (action === 'END_SESSION' && !confirm('End this customer session and clear its basket and customer details?')) return;
      button.disabled = true;
      try { await command(token, action, message); button.textContent = 'Sent'; setTimeout(refresh, 1000); }
      catch (error) { alert(error.message); }
      finally { setTimeout(() => { button.disabled = false; }, 1000); }
    }));

    grid.querySelectorAll('[data-rename-device]').forEach(button => button.addEventListener('click', async () => {
      const card = button.closest('[data-token]');
      const heading = card.querySelector('h2');
      const name = prompt('Name this iPad:', heading?.textContent || 'Customer iPad');
      if (!name || !name.trim()) return;
      button.disabled = true;
      try {
        await post(card.dataset.token, 'rename', {name: name.trim()});
        button.textContent = 'Renamed';
        setTimeout(refresh, 600);
      } catch (error) { alert(error.message); }
      finally { setTimeout(() => { button.disabled = false; button.textContent = 'Rename iPad'; }, 1200); }
    }));

    grid.querySelectorAll('[data-save-assignment]').forEach(button => button.addEventListener('click', async () => {
      const card = button.closest('[data-token]'); button.disabled = true;
      try {
        await post(card.dataset.token, 'assignment', {operator_name: card.querySelector('[data-operator]').value, ballroom: card.querySelector('[data-ballroom]').value});
        button.textContent = 'Saved'; setTimeout(refresh, 600);
      } catch (error) { alert(error.message); }
      finally { setTimeout(() => { button.disabled = false; button.textContent = 'Save assignment'; }, 1200); }
    }));
  }

  async function refresh() {
    try {
      const response = await fetch('/api/control/sessions', {cache: 'no-store'});
      if (!response.ok) throw new Error();
      const data = await response.json(); allSessions = data.sessions || []; populateEvents(allSessions); render();
    } catch (_) { /* retain the last successful device snapshot */ }
  }
  document.querySelector('[data-refresh]')?.addEventListener('click', refresh);
  eventFilter?.addEventListener('change', render); statusFilter?.addEventListener('change', render);
  allSessions = window.PIR_INITIAL_SESSIONS || []; populateEvents(allSessions); render(); setInterval(refresh, 4000);
})();
