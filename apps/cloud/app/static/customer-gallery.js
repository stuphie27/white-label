(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'cloud-lightbox';
  dialog.innerHTML = '<div class="cloud-lightbox-card"><button type="button" class="cloud-lightbox-close" aria-label="Close">×</button><img alt="Expanded event photograph"></div>';
  document.body.appendChild(dialog);
  const image = dialog.querySelector('img');
  const close = () => { if (dialog.open) dialog.close(); };
  dialog.querySelector('.cloud-lightbox-close').addEventListener('click', close);
  dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-expand-image]');
    if (!trigger) return;
    event.preventDefault();
    image.src = trigger.dataset.expandImage || trigger.querySelector('img')?.src || '';
    image.alt = trigger.dataset.expandAlt || 'Expanded event photograph';
    if (image.src) dialog.showModal();
  });
})();

// v1.6.0: match the iPad viewer's quick in-gallery filtering.
(() => {
  const search = document.querySelector('[data-cloud-photo-search]');
  if (!search) return;
  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    document.querySelectorAll('[data-cloud-photo]').forEach(card => {
      card.hidden = Boolean(q && !(card.dataset.search || '').includes(q));
    });
  });
})();

// v1.6.2: keep the customer in the gallery and show the basket as an in-page panel.
(() => {
  const drawer = document.querySelector('[data-basket-drawer]');
  if (!drawer) return;
  const url = drawer.dataset.basketUrl;
  async function loadAndOpen() {
    drawer.innerHTML = '<div class="basket-drawer-loading">Loading your basket…</div>';
    if (!drawer.open) drawer.showModal();
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(url, {
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
        headers: {'X-Requested-With': 'PirouetteBasketPanel'}
      });
      if (!response.ok) throw new Error(`Basket ${response.status}`);
      drawer.innerHTML = await response.text();
      drawer.querySelectorAll('[data-basket-close]').forEach(button => button.addEventListener('click', () => drawer.close()));
    } catch (error) {
      drawer.innerHTML = '<section class="basket-drawer-content"><header class="basket-drawer-header"><h2>Your basket</h2><button type="button" class="basket-drawer-close" data-basket-close>×</button></header><p>We could not load the basket panel. Please try again.</p></section>';
      drawer.querySelector('[data-basket-close]')?.addEventListener('click', () => drawer.close());
    } finally {
      window.clearTimeout(timeout);
    }
  }
  document.querySelectorAll('[data-open-basket]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); loadAndOpen(); }));
  drawer.addEventListener('click', event => { if (event.target === drawer) drawer.close(); });
  const params = new URLSearchParams(location.search);
  if (params.get('basket') === 'open') {
    loadAndOpen();
    params.delete('basket');
    const query = params.toString();
    history.replaceState({}, '', location.pathname + (query ? `?${query}` : '') + location.hash);
  }
})();


// v1.7.1: favourite photographs/videos in place, matching the event iPad flow.
// The current folder, search position and lightbox state are not discarded.
(() => {
  const forms = document.querySelectorAll('[data-cloud-favourite-form]');
  if (!forms.length) return;

  forms.forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    if (!button || button.disabled) return;
    const oldText = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: {'X-Requested-With': 'PirouetteFavourite'},
        credentials: 'same-origin',
        cache: 'no-store'
      });
      if (!response.ok) throw new Error(`Favourite ${response.status}`);
      const data = await response.json();
      const active = Boolean(data.active);
      button.classList.toggle('is-active', active);
      button.textContent = active ? '♥' : '♡';
      button.setAttribute('aria-label', active ? 'Remove favourite' : 'Save favourite');
      button.animate?.([
        {transform: 'scale(1)'},
        {transform: 'scale(1.28)'},
        {transform: 'scale(1)'}
      ], {duration: 260, easing: 'ease-out'});
      document.dispatchEvent(new CustomEvent('pirouette:cloud-favourite-changed', {detail: data}));
    } catch (error) {
      // Graceful fallback keeps the exact media/folder URL because the server
      // now honours return_to rather than forcing the event root.
      button.textContent = oldText;
      form.submit();
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  }));
})();

// v1.7.2: persistent favourites folders + returning customer parity.
(() => {
  const shell = document.querySelector('[data-gallery-slug]');
  const slug = shell?.dataset.gallerySlug || location.pathname.split('/g/')[1]?.split('/')[0] || '';
  if (!slug) return;

  const setup = document.querySelector('[data-cloud-favourite-setup]');
  const setupForm = setup?.querySelector('[data-cloud-favourite-profile-form]');
  const setupError = setup?.querySelector('[data-favourite-profile-error]');
  let pendingFavouriteForm = null;

  async function openSession(formData) {
    const response = await fetch(`/g/${encodeURIComponent(slug)}/favourites/session`, {
      method: 'POST', body: formData, credentials: 'same-origin', cache: 'no-store',
      headers: {'X-Requested-With': 'PirouetteFavouriteSession'}
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || 'We could not open your favourites folder.');
    return data;
  }

  document.querySelectorAll('[data-cloud-reopen-favourites]').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    const error = form.querySelector('[data-reopen-error]');
    if (error) error.hidden = true;
    const button = form.querySelector('button[type=submit]');
    if (button) button.disabled = true;
    try {
      const data = await openSession(new FormData(form));
      location.assign(data.url || `/g/${encodeURIComponent(slug)}/favourites`);
    } catch (err) {
      if (error) { error.textContent = err.message; error.hidden = false; }
    } finally { if (button) button.disabled = false; }
  }));

  if (setup && setupForm) {
    setup.querySelector('[data-cloud-favourite-close]')?.addEventListener('click', () => setup.close());
    setup.addEventListener('click', event => { if (event.target === setup) setup.close(); });
    setupForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (setupError) setupError.hidden = true;
      const button = setupForm.querySelector('button[type=submit]');
      if (button) button.disabled = true;
      try {
        const profileData = await openSession(new FormData(setupForm));
        if (profileData.name) {
          const copy = document.querySelector('.customer-experience-copy');
          if (copy) copy.innerHTML = `<small>YOUR SOPHIE’S PHOTOGRAPHY EXPERIENCE</small><strong>Welcome, ${String(profileData.name).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</strong>`;
        }
        setup.close();
        const form = pendingFavouriteForm;
        pendingFavouriteForm = null;
        if (form) form.requestSubmit();
        document.dispatchEvent(new CustomEvent('pirouette:customer-profile-ready'));
      } catch (err) {
        if (setupError) { setupError.textContent = err.message; setupError.hidden = false; }
      } finally { if (button) button.disabled = false; }
    });
  }

  // Capture the 409 emitted by the v1.7.1 in-place handler by wrapping fetch for
  // favourite forms before fallback navigation can happen.
  document.querySelectorAll('[data-cloud-favourite-form]').forEach(form => {
    form.addEventListener('submit', async event => {
      // The v1.7.1 listener also sees this event. stopImmediatePropagation keeps
      // one authoritative request and lets us handle first-favourite setup.
      event.preventDefault();
      event.stopImmediatePropagation();
      const button = form.querySelector('button');
      if (!button || button.disabled) return;
      button.disabled = true;
      try {
        const response = await fetch(form.action, {
          method:'POST', body:new FormData(form), credentials:'same-origin', cache:'no-store',
          headers:{'X-Requested-With':'PirouetteFavourite'}
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 409 && data.requires_profile) {
          pendingFavouriteForm = form;
          if (setup && !setup.open) setup.showModal();
          return;
        }
        if (!response.ok) throw new Error('Favourite could not be saved');
        const active = Boolean(data.active);
        button.classList.toggle('is-active', active);
        button.textContent = active ? '♥' : '♡';
        button.setAttribute('aria-label', active ? 'Remove favourite' : 'Save favourite');
      } catch (_) {
        form.submit();
      } finally { button.disabled = false; }
    }, true);
  });
})();

/* PIRouETTE LIVE FAVOURITES BOTTOM COUNTER */
(() => {
  const label = document.querySelector(
    '[data-favourites-bottom-label]'
  );

  if (!label) return;

  const baseLabel =
    label.dataset.label || 'Favourites';

  function render(count) {
    const safe = Math.max(
      0,
      Number(count) || 0
    );

    label.textContent =
      safe > 0
        ? `${baseLabel} (${safe})`
        : baseLabel;
  }

  document.addEventListener(
    'pirouette:cloud-favourite-changed',
    event => {
      const detail = event.detail || {};

      if (detail.count !== undefined) {
        render(detail.count);
        return;
      }

      const match =
        label.textContent.match(/\((\d+)\)/);

      const current =
        match ? Number(match[1]) : 0;

      render(
        current +
        (detail.active ? 1 : -1)
      );
    }
  );
})();

// PIRouette customer live activity
(() => {
  const galleryMatch =
    window.location.pathname.match(
      /^\/g\/([^\/]+)/
    );

  if (!galleryMatch) return;

  const slug = decodeURIComponent(
    galleryMatch[1]
  );

  const endpoint =
    `/g/${encodeURIComponent(slug)}/activity`;

  const sendActivity = (action = "heartbeat") => {
    const params =
      new URLSearchParams(window.location.search);

    const folder = params.get("folder") || "";

    let area = "gallery";

    if (
      window.location.pathname.includes(
        "/favourites"
      )
    ) {
      area = "favourites";
    } else if (
      window.location.pathname.includes(
        "/basket"
      )
    ) {
      area = "basket";
    } else if (
      window.location.pathname.includes(
        "/checkout"
      )
    ) {
      area = "checkout";
    }

    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With":
          "PirouetteCustomerActivity",
      },
      body: JSON.stringify({
        action,
        area,
        folder,
      }),
      keepalive: true,
    }).catch(() => {});
  };

  // Maintain live presence while the customer is browsing.
  sendActivity("heartbeat");

  window.setInterval(
    () => sendActivity("heartbeat"),
    60000
  );

  // Count a photograph only when the customer deliberately
  // opens/taps the protected preview - never thumbnail loading.
  document.addEventListener(
    "click",
    (event) => {
      const target = event.target.closest(
        [
          "[data-protected-preview]",
          "[data-photo-preview]",
          "[data-expand-image]",
          "[data-lightbox]",
          "a[href*='/assets/']",
        ].join(",")
      );

      if (!target) return;

      sendActivity("photo_view");
    },
    { passive: true }
  );
})();
