(() => {
  const TIMEOUT_MS = 60 * 1000;
  const WARNING_MS = 10 * 1000;
  const HEARTBEAT_MS = 4000;
  const attract = document.querySelector('[data-kiosk-attract]');
  const customer = document.querySelector('[data-customer-session]');
  const slug = attract?.dataset.kioskSlug || customer?.dataset.customerSlug;
  if (!slug) return;

  const sessionKey = `pirouette-customer-session:${slug}`;
  const savedKey = `pirouette-customer-saved:${slug}`;
  const emailKey = `pirouette-customer-email:${slug}`;
  const favouritesTokenKey = `pirouette-favourites-token:${slug}`;
  const favouritesEmailKey = `pirouette-favourites-email:${slug}`;
  const randomId = () => window.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let deviceId = localStorage.getItem('pp-device-id') || randomId();
  localStorage.setItem('pp-device-id', deviceId);
  const ua = navigator.userAgent || '';
  const isIPad = /iPad/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let deviceName = localStorage.getItem('pp-device-name') || '';
  if (!deviceName) {
    const suggested = `${isIPad ? 'Customer iPad' : 'Customer device'} ${deviceId.slice(0,4).toUpperCase()}`;
    const entered = window.prompt('Name this customer device so staff can identify it:', suggested);
    deviceName = String(entered || suggested).trim().slice(0, 80) || suggested;
    localStorage.setItem('pp-device-name', deviceName);
  }
  let sessionToken = sessionStorage.getItem('pp-session-token') || randomId() + randomId();
  sessionStorage.setItem('pp-session-token', sessionToken);
  let lastActivity = Date.now();
  let locked = false;
  let timer;
  let warningTimer;

  // Keep the language selector inside the customer header instead of floating over the page.
  const languageControl = document.querySelector('[data-language-control]');
  const viewerHero = document.querySelector('.viewer-hero');
  if (languageControl && viewerHero) viewerHero.prepend(languageControl);

  function basket() {
    try { return JSON.parse(localStorage.getItem(`pirouette-basket:${slug}`) || '[]'); } catch (_) { return []; }
  }
  function basketCount() { return basket().reduce((n, i) => n + Math.max(1, Number(i.quantity) || 1), 0); }
  function pageState() {
    const path = location.pathname;
    if (attract) return {page:'Welcome screen',status:'idle'};
    if (path.includes('/basket')) return {page:'Basket and checkout',status:'checkout'};
    if (path.includes('/favourites')) return {page:'Favourites',status:'browsing'};
    return {page:'Photo gallery',status:'browsing'};
  }
  function browsingState() {
    return {path: location.pathname + location.search, scroll_y: window.scrollY || 0, updated_at: new Date().toISOString()};
  }
  async function api(path, data) {
    const response = await fetch(path, {method:'POST', cache:'no-store', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data || {})});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || 'Something went wrong');
    return body;
  }
  function updateFavouritesLink(data = {}) {
    const token = data.favourites_token || localStorage.getItem(favouritesTokenKey) || '';
    const stored = Number(localStorage.getItem(`pirouette-favourites-count:${slug}`) || 0);
    const count = Number.isFinite(Number(data.favourites_count)) ? Number(data.favourites_count) : stored;
    localStorage.setItem(`pirouette-favourites-count:${slug}`, String(Math.max(0, count)));
    document.querySelectorAll('[data-session-favourites-link]').forEach(link => {
      link.hidden = false;
      if (token) link.href = `/view/${encodeURIComponent(slug)}/favourites?token=${encodeURIComponent(token)}`;
      else link.href = '#';
    });
    document.querySelectorAll('[data-favourites-count]').forEach(badge => { badge.textContent = String(Math.max(0, count)); });
  }
  function storeSession(data) {
    localStorage.setItem(sessionKey, data.token);
    localStorage.setItem(savedKey, data.saved ? '1' : '0');
    if (data.email) localStorage.setItem(emailKey, data.email); else localStorage.removeItem(emailKey);
    if (data.customer_name) localStorage.setItem(`pirouette-customer-name:${slug}`, data.customer_name);
    if (data.favourites_token) {
      localStorage.setItem(favouritesTokenKey, data.favourites_token);
      if (data.email) localStorage.setItem(favouritesEmailKey, data.email);
    }
    updateFavouritesLink(data);
  }
  async function start(mode, email='', preferences={}) {
    const data = await api(`/api/view/${encodeURIComponent(slug)}/session/start`, {mode,email,...preferences});
    storeSession(data);
    location.assign(`/view/${encodeURIComponent(slug)}`);
  }
  async function sync() {
    const token = localStorage.getItem(sessionKey);
    if (!token || attract) return;
    try { await api(`/api/view/${encodeURIComponent(slug)}/session/sync`, {token,basket:basket(),state:browsingState()}); } catch (_) {}
  }
  async function resume(email) {
    const data = await api(`/api/view/${encodeURIComponent(slug)}/session/resume`, {email});
    storeSession({...data,saved:true});
    localStorage.setItem(`pirouette-basket:${slug}`, JSON.stringify(data.basket || []));
    const target = data.state?.path && data.state.path.startsWith(`/view/${slug}`) ? data.state.path : `/view/${slug}`;
    location.assign(target);
  }
  async function saveCurrent(email, preferences={}) {
    let token = localStorage.getItem(sessionKey);
    if (!token) {
      const created = await api(`/api/view/${encodeURIComponent(slug)}/session/start`, {mode:'browse'});
      token = created.token; storeSession(created);
    }
    const data = await api(`/api/view/${encodeURIComponent(slug)}/session/save`, {token,email,...preferences});
    if (preferences.customer_name) data.customer_name = preferences.customer_name;
    storeSession({...data, saved:true});
    await sync();
    return data;
  }

  async function clearCustomerData({closeServer=true}={}) {
    const token = localStorage.getItem(sessionKey);
    if (closeServer && token) {
      try { await api(`/api/view/${encodeURIComponent(slug)}/session/close`, {token}); } catch (_) {}
    }
    for (let i=localStorage.length-1;i>=0;i--) {
      const key=localStorage.key(i);
      if (key && key.startsWith('pirouette-')) localStorage.removeItem(key);
    }
    sessionStorage.clear();
    if ('caches' in window) try { await Promise.all((await caches.keys()).map(n=>caches.delete(n))); } catch (_) {}
    try { await fetch('/api/kiosk/end-session',{method:'POST',cache:'no-store',keepalive:true}); } catch (_) {}
  }
  async function finish({redirect=true,target=''}={}) {
    await sync();
    await clearCustomerData();
    if (redirect) location.replace(target || `/kiosk/${encodeURIComponent(slug)}`);
  }
  function warningOverlay() {
    let el=document.getElementById('idle-warning-overlay');
    if (!el) {
      el=document.createElement('div'); el.id='idle-warning-overlay'; el.className='idle-warning-overlay'; el.hidden=true;
      el.innerHTML='<div><h2>Are you still there?</h2><p>This iPad will clear your visit in <strong data-idle-count>10</strong> seconds to protect your privacy.</p><div class="idle-warning-actions"><button class="button" type="button" data-still-here>Yes, I’m still here</button><button class="button button-secondary" type="button" data-close-idle-session>Close my session</button></div></div>';
      document.body.appendChild(el);
      el.querySelector('[data-still-here]').addEventListener('click',()=>{el.hidden=true; resetTimer();});
      el.querySelector('[data-close-idle-session]').addEventListener('click',()=>finish({target:`/kiosk/${encodeURIComponent(slug)}`}));
    }
    return el;
  }
  function showIdleWarning() {
    const el=warningOverlay(); el.hidden=false; let remaining=10;
    const count=el.querySelector('[data-idle-count]'); count.textContent=remaining;
    const interval=setInterval(()=>{remaining-=1; count.textContent=Math.max(0,remaining);},1000);
    warningTimer=setTimeout(()=>{clearInterval(interval); finish();},WARNING_MS);
  }
  function resetTimer() {
    lastActivity=Date.now();
    clearTimeout(timer); clearTimeout(warningTimer);
    const overlay=document.getElementById('idle-warning-overlay'); if (overlay) overlay.hidden=true;
    if (customer && !locked) timer=setTimeout(showIdleWarning,TIMEOUT_MS);
  }

  async function startFreshBrowsing() {
    // The welcome screen always begins a clean iPad customer session. Saved
    // favourites remain safely on the server and can be reopened by email
    // from the Favourites button, but no previous customer's local token,
    // basket or retention choice is allowed to leak into the next visit.
    await clearCustomerData();
    sessionToken = randomId() + randomId();
    sessionStorage.setItem('pp-session-token', sessionToken);
    await start('browse');
  }
  // v1.7.14: make basket navigation deterministic on supervised iPad/Web Clip.
  // iPadOS can suppress the synthetic click for a link when our kiosk gesture
  // protection calls preventDefault() on touchend. Handle the basket link on the
  // element itself so a single customer tap always reaches checkout.
  document.querySelectorAll('[data-basket-navigation]').forEach(link => {
    let touchNavigated = false;
    const go = () => {
      const href = link.getAttribute('href');
      if (href) window.location.assign(href);
    };
    link.addEventListener('touchend', event => {
      if (event.touches?.length) return;
      event.preventDefault();
      touchNavigated = true;
      go();
      setTimeout(() => { touchNavigated = false; }, 500);
    }, {passive:false});
    link.addEventListener('click', event => {
      if (touchNavigated) { event.preventDefault(); return; }
      event.preventDefault();
      go();
    });
  });

  attract?.querySelector('[data-start-browsing]')?.addEventListener('click',()=>startFreshBrowsing());
  document.querySelectorAll('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog')?.close()));

  function ensureOverlay() { let o=document.getElementById('remote-assistance-overlay'); if(!o){o=document.createElement('div');o.id='remote-assistance-overlay';o.className='remote-assistance-overlay';o.hidden=true;o.innerHTML='<div><h2>Staff assistance</h2><p data-remote-message>A member of staff is coming to help you.</p></div>';document.body.appendChild(o);}return o; }
  function showMessage(m,p=false){const o=ensureOverlay();o.querySelector('[data-remote-message]').textContent=m;o.hidden=false;if(!p)setTimeout(()=>{if(!locked)o.hidden=true;},8000);}
  async function heartbeat(){const s=pageState();try{const r=await fetch('/api/kiosk/heartbeat',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_token:sessionToken,device_id:deviceId,device_name:deviceName,event_slug:slug,page:s.page,status:locked?'locked':s.status,basket_count:basketCount(),idle_seconds:Math.floor((Date.now()-lastActivity)/1000),user_agent:navigator.userAgent})});if(!r.ok)return;const d=await r.json();if(d.device_name&&d.device_name!==deviceName){deviceName=String(d.device_name).slice(0,80);localStorage.setItem('pp-device-name',deviceName);}if(d.command==='END_SESSION'||d.command==='SHOW_WELCOME')await finish();else if(d.command==='RELOAD'){const openDialog=document.querySelector('dialog[open]');const playingVideo=Array.from(document.querySelectorAll('video')).some(v=>!v.paused&&!v.ended);if(!openDialog&&!playingVideo)location.reload();}else if(d.command==='CLEAR_BASKET'){localStorage.removeItem(`pirouette-basket:${slug}`);showMessage('Your basket has been cleared.');}else if(d.command==='LOCK'){locked=true;showMessage(d.message||'This device is paused.',true);}else if(d.command==='UNLOCK'){locked=false;ensureOverlay().hidden=true;resetTimer();}else if(d.command==='SHOW_MESSAGE')showMessage(d.message||'A member of staff is coming to help you.');}catch(_){}}

  updateFavouritesLink();
  window.PirouettePrivacy={clearCustomerData,finish,sync,saveCurrent};
  ['pointerdown','touchstart','keydown','scroll'].forEach(n=>window.addEventListener(n,resetTimer,{passive:true}));
  document.querySelectorAll('[data-end-session]').forEach(b=>b.addEventListener('click',async()=>{if(confirm('End this visit and clear this iPad?'))await finish({target:`/kiosk/${encodeURIComponent(slug)}`});}));
  if (customer) { if(!localStorage.getItem(sessionKey)) start('browse'); else resetTimer(); setInterval(sync,15000); }
  heartbeat(); setInterval(heartbeat,HEARTBEAT_MS);
})();

// v1.7.2: returning customers can reopen their favourites from the iPad start screen.
(() => {
  const root = document.querySelector('[data-kiosk-attract]');
  const form = root?.querySelector('[data-kiosk-reopen-favourites]');
  if (!root || !form) return;
  const slug = root.dataset.kioskSlug || '';
  const error = form.querySelector('[data-kiosk-reopen-error]');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (error) error.hidden = true;
    const button = form.querySelector('button[type=submit]');
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/view/${encodeURIComponent(slug)}/favourites/reopen`, {
        method:'POST', cache:'no-store', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({customer_name:form.customer_name.value.trim(),email:form.customer_email.value.trim()})
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'We could not find your favourites.');
      if (data.token) localStorage.setItem(`pirouette-favourites-token:${slug}`, data.token);
      if (data.email) localStorage.setItem(`pirouette-favourites-email:${slug}`, data.email);
      if (data.customer_name) localStorage.setItem(`pirouette-customer-name:${slug}`, data.customer_name);
      localStorage.setItem(`pirouette-favourites-count:${slug}`, String(data.count || 0));
      location.assign(data.url || `/view/${encodeURIComponent(slug)}/favourites`);
    } catch (err) {
      if (error) { error.textContent = err.message; error.hidden = false; }
    } finally { if (button) button.disabled = false; }
  });
})();
