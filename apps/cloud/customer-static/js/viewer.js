(() => {
  const page = document.querySelector('[data-viewer-slug]');
  if (!page) return;
  const slug = page.dataset.viewerSlug;
  const currentFolder = page.dataset.currentFolder || '';
  const query = page.dataset.query || '';
  const grid = document.getElementById('viewer-grid');

  // Customer journey:
  // At the photo root customers see folders only.
  // Live photographs may be added inside a folder or for search results.
  const allowLivePhotoCards = Boolean(currentFolder.trim() || query.trim());
  const status = document.getElementById('viewer-status');
  const empty = document.getElementById('viewer-empty');
  const basketKey = `pirouette-basket:${slug}`;
  const tokenKey = `pirouette-favourites-token:${slug}`;
  const emailKey = `pirouette-favourites-email:${slug}`;
  const returnKey = `pirouette-favourites-return:${slug}`;
  const scrollKey = `pirouette-favourites-scroll:${slug}`;
  const basket = JSON.parse(localStorage.getItem(basketKey) || '[]');
  let favouriteToken = page.dataset.favouritesToken || localStorage.getItem(tokenKey) || '';
  let pendingFavourite = null; // {type: 'photo'|'video', id: number|string}
  let favourites = new Set();
  const favouriteCountKey = `pirouette-favourites-count:${slug}`;
  function setFavouriteCount(count) {
    const safe = Math.max(0, Number(count) || 0);
    localStorage.setItem(favouriteCountKey, String(safe));
    document.querySelectorAll('[data-favourites-count]').forEach(el => { el.textContent = String(safe); });
    window.dispatchEvent(new CustomEvent('pirouette:favourites-count', {detail: {count: safe}}));
  }
  function changeFavouriteCount(delta) {
    const current = Number(localStorage.getItem(favouriteCountKey) || 0);
    setFavouriteCount(current + Number(delta || 0));
  }
  window.PirouetteFavouriteCount = {set: setFavouriteCount, change: changeFavouriteCount};

  // The favourites page is rendered from the database. Seed the client-side
  // state from the cards already on the page so the first heart tap removes
  // an item instead of trying to save it again.
  if (page.hasAttribute('data-favourites-page')) {
    favourites = new Set(
      Array.from(document.querySelectorAll('.viewer-photo[data-photo-id]'))
        .map(card => String(card.dataset.photoId))
    );
    const savedVideoCount = document.querySelectorAll('[data-video-id]').length;
    setFavouriteCount(favourites.size + savedVideoCount);
  } else {
    setFavouriteCount(localStorage.getItem(favouriteCountKey) || 0);
  }

  const basketTotal = () => basket.reduce((total, item) => total + Math.max(1, Number(item.quantity) || 1), 0);
  const updateBasketCount = () => {
    const count = basketTotal();
    document.querySelectorAll('.basket-count, [data-basket-count]').forEach(el => { el.textContent = String(count); });
    window.dispatchEvent(new CustomEvent('pirouette:basket-count', {detail: {slug, count}}));
  };
  const saveBasket = () => { localStorage.setItem(basketKey, JSON.stringify(basket)); updateBasketCount(); };
  updateBasketCount();

  async function stuphieVerifiedResume() {
    try {
      const response = await fetch(
        `/api/view/${encodeURIComponent(slug)}/session/verified-resume`,
        {
          method: 'POST',
          cache: 'no-store',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({})
        }
      );

      if (response.status === 401) {
        return null;
      }

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail ||
          'Unable to restore verified customer session'
        );
      }

      if (!data.verified) {
        return null;
      }

      if (data.token) {
        localStorage.setItem(
          `pirouette-customer-session:${slug}`,
          data.token
        );

        localStorage.setItem(
          `pirouette-customer-saved:${slug}`,
          '1'
        );
      }

      if (data.email) {
        localStorage.setItem(
          `pirouette-customer-email:${slug}`,
          data.email
        );

        localStorage.setItem(
          `pirouette-favourites-email:${slug}`,
          data.email
        );
      }

      if (data.customer_name) {
        localStorage.setItem(
          `pirouette-customer-name:${slug}`,
          data.customer_name
        );

        window.dispatchEvent(
          new CustomEvent(
            'pirouette:customer-name',
            {
              detail: {
                name: data.customer_name
              }
            }
          )
        );
      }

      if (data.favourites_token) {
        favouriteToken =
          data.favourites_token;

        localStorage.setItem(
          tokenKey,
          data.favourites_token
        );
      }

      if (
        Number.isFinite(
          Number(data.favourites_count)
        )
      ) {
        setFavouriteCount(
          Number(data.favourites_count)
        );
      }

      if (Array.isArray(data.basket)) {
        basket.splice(
          0,
          basket.length,
          ...data.basket
        );

        saveBasket();
      }

      if (data.retention_choice) {
        localStorage.setItem(
          `pirouette-favourites-retention:${slug}`,
          String(
            data.retention_choice
          )
        );
      }

      window.dispatchEvent(
        new CustomEvent(
          'stuphie:verified-customer',
          {
            detail: {
              customer_id:
                data.customer_id,
              email:
                data.email || '',
              name:
                data.customer_name || '',
              device_label:
                data.device_label || ''
            }
          }
        )
      );

      return data;
    } catch (_) {
      return null;
    }
  }

  window.STUPHIEVerifiedResume =
    stuphieVerifiedResume;

  window.addEventListener('storage', event => {
    if (event.key !== basketKey) return;
    try {
      const latest = JSON.parse(event.newValue || '[]');
      basket.splice(0, basket.length, ...latest);
      updateBasketCount();
    } catch (_) {}
  });

  function rememberCurrentScreen() {
    const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    sessionStorage.setItem(returnKey, returnTo);
    sessionStorage.setItem(scrollKey, String(window.scrollY || 0));
    return returnTo;
  }

  function withReturnTo(url) {
    const target = new URL(url, window.location.origin);
    target.searchParams.set('return_to', rememberCurrentScreen());
    return `${target.pathname}${target.search}${target.hash}`;
  }

  function closeFavourites() {
    const fallback = page.dataset.favouritesReturnTo || sessionStorage.getItem(returnKey) || `/view/${encodeURIComponent(slug)}`;
    const referrer = document.referrer ? new URL(document.referrer, window.location.origin) : null;
    const canGoBack = referrer && referrer.origin === window.location.origin && !referrer.pathname.endsWith('/favourites');
    if (canGoBack && window.history.length > 1) window.history.back();
    else window.location.assign(fallback);
  }

  document.querySelectorAll('[data-close-favourites]').forEach(button => {
    button.addEventListener('click', closeFavourites);
  });

  if (!page.hasAttribute('data-favourites-page')) {
    stuphieVerifiedResume();

    const savedReturn = sessionStorage.getItem(returnKey);
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (savedReturn === current) {
      const savedScroll = Number(sessionStorage.getItem(scrollKey) || 0);
      window.requestAnimationFrame(() => window.scrollTo(0, savedScroll));
    }
  }

  async function openSession(email, preferences = null) {
    const payload = preferences ? {email, configure_retention: true, ...preferences} : {email};
    const response = await fetch(`/api/view/${encodeURIComponent(slug)}/favourites/session`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to open favourites');
    favouriteToken = data.token;
    localStorage.setItem(tokenKey, data.token);
    localStorage.setItem(emailKey, data.email);
    if (preferences?.customer_name) { const customerName=String(preferences.customer_name).trim(); localStorage.setItem(`pirouette-customer-name:${slug}`, customerName); window.dispatchEvent(new CustomEvent('pirouette:customer-name',{detail:{name:customerName}})); }
    else if (data.customer_name) { const customerName=String(data.customer_name).trim(); localStorage.setItem(`pirouette-customer-name:${slug}`, customerName); window.dispatchEvent(new CustomEvent('pirouette:customer-name',{detail:{name:customerName}})); }
    if (preferences?.retention_choice) localStorage.setItem(`pirouette-favourites-retention:${slug}`, String(preferences.retention_choice));
    if (preferences?.retention_choice === 'vault_year') localStorage.setItem(`pirouette-favourites-payment:${slug}`, 'basket');
    else localStorage.removeItem(`pirouette-favourites-payment:${slug}`);
    favourites = new Set((data.photo_ids || []).map(String));
    setFavouriteCount((data.photo_ids || []).length + (data.video_ids || []).length);
    return data;
  }

  async function setServerFavourite(photoId, active) {
    const response = await fetch(`/api/view/${encodeURIComponent(slug)}/favourites/${encodeURIComponent(favouriteToken)}/${photoId}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({active})
    });
    if (!response.ok) throw new Error('Unable to save favourite');
  }

  const dialog = document.getElementById('favourites-email-dialog');
  const emailForm = document.querySelector('[data-favourites-email-form]');
  const formError = document.querySelector('[data-favourites-error]');
  const retentionInputs = emailForm?.querySelectorAll('input[name="retention_choice"]') || [];
  const nameInput = emailForm?.querySelector('input[name="customer_name"]');
  const emailInput = emailForm?.querySelector('input[name="email"]');
  const returningStatus = emailForm?.querySelector('[data-returning-customer-status]');
  let lookupTimer = null;
  let lastLookup = '';

  function applySavedPreference(choice) {
    const saved = String(choice || localStorage.getItem(`pirouette-favourites-retention:${slug}`) || 'temporary_link');
    retentionInputs.forEach(input => { input.checked = input.value === (saved === 'vault_year' ? 'vault_year' : 'temporary_link'); });
  }

  function requestEmail(photoId = null) {
    pendingFavourite = photoId ? {type: 'photo', id: photoId} : pendingFavourite;
    if (dialog && typeof dialog.showModal === 'function') {
      if (emailInput) emailInput.value = localStorage.getItem(emailKey) || '';
      if (nameInput) nameInput.value = localStorage.getItem(`pirouette-customer-name:${slug}`) || '';
      applySavedPreference();
      if (returningStatus) returningStatus.hidden = true;
      if (formError) formError.hidden = true;
      dialog.showModal();
      window.requestAnimationFrame(() => (nameInput?.value ? emailInput : nameInput)?.focus());
    } else {
      const email = window.prompt('Enter your email address to save or reopen your favourites');
      if (email) openSession(email, {retention_choice: 'temporary_link'}).then(() => {
        if (photoId) toggleFavourite(photoId, true);
      }).catch(error => window.alert(error.message));
    }
  }

  function cancelFavouriteEmailDialog() {
    pendingFavourite = null;
    if (formError) { formError.textContent = ''; formError.hidden = true; }
    if (dialog?.open) dialog.close('cancel');
  }

  document.querySelector('[data-cancel-favourites-email]')?.addEventListener('click', cancelFavouriteEmailDialog);
  dialog?.addEventListener('cancel', event => { event.preventDefault(); cancelFavouriteEmailDialog(); });
  dialog?.addEventListener('click', event => { if (event.target === dialog) cancelFavouriteEmailDialog(); });

  async function lookupExistingFavouriteFolder() {
    const email = emailInput?.value.trim().toLowerCase() || '';
    if (!emailInput?.validity.valid || !email || email === lastLookup) return;
    lastLookup = email;
    if (returningStatus) { returningStatus.textContent = 'Checking for your existing favourites…'; returningStatus.hidden = false; }
    try {
      const response = await fetch(`/api/view/${encodeURIComponent(slug)}/session/resume`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email})
      });
      if (response.status === 404) {
        if (returningStatus) returningStatus.hidden = true;
        return;
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Unable to reopen favourites');
      if (!data.favourites_token) return;

      favouriteToken = data.favourites_token;
      localStorage.setItem(tokenKey, data.favourites_token);
      localStorage.setItem(emailKey, email);
      if (data.customer_name) {
        const customerName=String(data.customer_name).trim();
        localStorage.setItem(`pirouette-customer-name:${slug}`, customerName);
        window.dispatchEvent(new CustomEvent('pirouette:customer-name',{detail:{name:customerName}}));
        if (nameInput && !nameInput.value.trim()) nameInput.value = String(data.customer_name).trim();
      }
      if (data.retention_choice) {
        localStorage.setItem(`pirouette-favourites-retention:${slug}`, String(data.retention_choice));
        applySavedPreference(data.retention_choice);
      }
      if (returningStatus) {
        returningStatus.textContent = 'Welcome back — your existing favourites will be used. Carry on below.';
        returningStatus.hidden = false;
      }
    } catch (error) {
      if (returningStatus) { returningStatus.textContent = error.message; returningStatus.hidden = false; }
    }
  }

  emailInput?.addEventListener('input', () => {
    window.clearTimeout(lookupTimer);
    lastLookup = '';
    if (returningStatus) returningStatus.hidden = true;
    lookupTimer = window.setTimeout(lookupExistingFavouriteFolder, 650);
  });
  emailInput?.addEventListener('blur', lookupExistingFavouriteFolder);

  emailForm?.addEventListener('submit', async event => {
    event.preventDefault();
    if (formError) formError.hidden = true;
    try {
      const form = new FormData(emailForm);
      const choice = form.get('retention_choice') === 'vault_year' ? 'vault_year' : 'temporary_link';
      const data = await openSession(form.get('email'), {
        customer_name: form.get('customer_name'),
        retention_choice: choice,
        reminder_opt_in: false,
        marketing_opt_in: form.get('marketing_opt_in') === 'on'
      });
      dialog?.close();
      if (pendingFavourite) {
        const pending = pendingFavourite; pendingFavourite = null;
        if (pending.type === 'video') {
          const response = await fetch(`/api/view/${encodeURIComponent(slug)}/favourites/${encodeURIComponent(favouriteToken)}/video/${pending.id}`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({active: true})
          });
          if (!response.ok) throw new Error('Unable to save video favourite');
          document.dispatchEvent(new CustomEvent('pirouette:favourite-created', {detail: pending}));
        } else {
          await toggleFavourite(pending.id, true);
          renderLargePhoto(currentPhotoIndex);
          bouncePreviewAction(largeFavouriteButton, 'preview-heart-pop');
        }
      } else {
        window.location.assign(withReturnTo(data.url));
      }
    } catch (error) {
      if (formError) { formError.textContent = error.message; formError.hidden = false; }
    }
  });

  document.addEventListener('pirouette:first-favourite', event => {
    if (favouriteToken) return;
    const detail = event.detail || {};
    if (!detail.mediaType || !detail.mediaId) return;
    pendingFavourite = {type: detail.mediaType, id: detail.mediaId};
    requestEmail();
    pendingFavourite = {type: detail.mediaType, id: detail.mediaId};
  });

  document.querySelectorAll('[data-open-favourites]').forEach(button => button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (favouriteToken) {
      window.location.assign(withReturnTo(`/view/${encodeURIComponent(slug)}/favourites?token=${encodeURIComponent(favouriteToken)}`));
    } else {
      requestEmail();
    }
  }));

  document.addEventListener('click', event => {
    const control = event.target.closest('[data-session-favourites-link]');
    if (!control || control.hasAttribute('data-open-favourites')) return;
    event.preventDefault();
    if (favouriteToken) {
      window.location.assign(withReturnTo(`/view/${encodeURIComponent(slug)}/favourites?token=${encodeURIComponent(favouriteToken)}`));
    } else {
      requestEmail();
    }
  });

  async function toggleFavourite(id, forceActive = null) {
    if (!favouriteToken) { requestEmail(id); return; }
    const active = forceActive === null ? !favourites.has(String(id)) : forceActive;
    await setServerFavourite(id, active);
    active ? favourites.add(String(id)) : favourites.delete(String(id));
    changeFavouriteCount(active ? 1 : -1);
    document.querySelectorAll(`[data-photo-id="${id}"] .favourite`).forEach(button => setFavourite(button, id));
    if (page.hasAttribute('data-favourites-page') && !active) document.querySelector(`[data-photo-id="${id}"]`)?.remove();
  }

  function setFavourite(button, id) {
    const active = favourites.has(String(id));
    if (button.hasAttribute('data-unfavourite-button')) {
      button.innerHTML = active
        ? '<span aria-hidden="true">👎</span><span class="remove-label">Remove</span>'
        : '<span aria-hidden="true">♡</span><span class="remove-label">Favourite</span>';
    } else {
      button.textContent = active ? '♥' : '♡';
    }
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.setAttribute('aria-label', active ? 'Remove favourite' : 'Save favourite');
  }
  function addToBasket(id, button) {
    const photoId = Number(id);
    if (!basket.some(item => Number(item.photo_id) === photoId)) basket.push({photo_id: photoId, product_code: 'print_5x7', quantity: 1});
    saveBasket();
    if (button) { button.innerHTML = '<span aria-hidden="true">✓</span>'; button.classList.add('is-added'); button.setAttribute('aria-label', 'Photograph added to basket'); button.setAttribute('title', 'Added to basket'); }
  }
  const photoViewerDialog = document.getElementById('photo-viewer-dialog');
  const photoViewerImage = document.querySelector('[data-photo-viewer-image]');
  const photoViewerStage = document.querySelector('[data-photo-viewer-stage]');
  const photoViewerPosition = document.querySelector('[data-photo-viewer-position]');
  const previousPhotoButton = document.querySelector('[data-photo-viewer-previous]');
  const nextPhotoButton = document.querySelector('[data-photo-viewer-next]');
  const largeFavouriteButton = document.querySelector('[data-photo-viewer-favourite]');
  const largeAddButton = document.querySelector('[data-photo-viewer-add]');
  const largeFavouriteLabel = document.querySelector('[data-preview-favourite-label]');
  const largeFavouriteIcon = document.querySelector('[data-preview-heart-icon]');
  const largeBasketLabel = document.querySelector('[data-preview-basket-label]');
  const largeCartLink = document.querySelector('[data-photo-viewer-cart]');
  const photoProtectionDialog = document.getElementById('photo-protection-dialog');
  const protectionKey = `pirouette-photo-protection-confirmed:${slug}`;
  let pendingPhotoIndex = 0;
  let currentPhotoIndex = 0;
  let swipeStartX = null;
  let previewControlsTimer = null;

  function galleryPhotos() {
    return Array.from(document.querySelectorAll('.viewer-photo')).map(card => {
      const image = card.querySelector('img');
      return {
        id: card.dataset.photoId,
        source: image?.currentSrc || image?.src || '',
        alt: image?.alt || 'Event photograph'
      };
    }).filter(photo => photo.source);
  }

  function renderLargePhoto(index) {
    if (!photoViewerImage) return;
    const photos = galleryPhotos();
    if (!photos.length) return;
    currentPhotoIndex = (index + photos.length) % photos.length;
    const photo = photos[currentPhotoIndex];
    photoViewerImage.src = photo.source;
    photoViewerImage.alt = photo.alt || 'Large event photograph preview';
    if (photoViewerPosition) photoViewerPosition.textContent = `${currentPhotoIndex + 1} of ${photos.length} ·`;
    const showNavigation = photos.length > 1;
    if (previousPhotoButton) previousPhotoButton.hidden = !showNavigation;
    if (nextPhotoButton) nextPhotoButton.hidden = !showNavigation;
    if (largeFavouriteButton) {
      const active = favourites.has(String(photo.id));
      if (largeFavouriteIcon) largeFavouriteIcon.textContent = active ? '♥' : '♡';
      if (largeFavouriteLabel) largeFavouriteLabel.textContent = active ? 'Favourited' : 'Favourite';
      largeFavouriteButton.setAttribute('aria-pressed', active ? 'true' : 'false');
      largeFavouriteButton.setAttribute('aria-label', active ? 'Remove photograph from favourites' : 'Save photograph as a favourite');
    }
    if (largeAddButton) {
      const added = basket.some(item => Number(item.photo_id) === Number(photo.id));
      if (largeBasketLabel) largeBasketLabel.textContent = added ? 'Added to basket' : 'Add to basket';
      largeAddButton.setAttribute('aria-pressed', added ? 'true' : 'false');
      largeAddButton.setAttribute('aria-label', added ? 'Photograph added to basket' : 'Add photograph to basket');
    }
  }


  function bouncePreviewAction(element, successClass = '') {
    if (!element) return;
    element.classList.remove('preview-action-bounce', 'preview-action-success', 'preview-heart-pop');
    void element.offsetWidth;
    element.classList.add('preview-action-bounce');
    if (successClass) element.classList.add(successClass);
    window.setTimeout(() => element.classList.remove('preview-action-bounce', successClass), 700);
  }

  function bounceBasketIndicators() {
    document.querySelectorAll('.basket-count, [data-basket-count]').forEach(count => bouncePreviewAction(count, 'preview-action-success'));
    bouncePreviewAction(largeCartLink, 'preview-action-success');
  }

  function revealPreviewControls() {
    if (!photoViewerDialog) return;
    photoViewerDialog.classList.add('preview-controls-visible');
    window.clearTimeout(previewControlsTimer);
    previewControlsTimer = window.setTimeout(() => {
      photoViewerDialog.classList.remove('preview-controls-visible');
    }, 1800);
  }

  function recordLargePhotoView(photoId) {
    if (!photoId) return;
    fetch(`/api/view/${encodeURIComponent(slug)}/photo-views/${encodeURIComponent(photoId)}`, {
      method: 'POST',
      headers: {'X-Requested-With': 'XMLHttpRequest'},
      keepalive: true
    }).catch(() => {});
  }

  function showLargePhoto(index) {
    if (!photoViewerDialog || !photoViewerImage) return;
    renderLargePhoto(index);
    const openedPhoto = galleryPhotos()[currentPhotoIndex];
    recordLargePhotoView(openedPhoto?.id);
    if (typeof photoViewerDialog.showModal === 'function' && !photoViewerDialog.open) photoViewerDialog.showModal();
    revealPreviewControls();
  }

  function requestLargePhoto(index) {
    pendingPhotoIndex = index;
    if (sessionStorage.getItem(protectionKey) === 'yes' || !photoProtectionDialog || typeof photoProtectionDialog.showModal !== 'function') {
      showLargePhoto(index);
      return;
    }
    photoProtectionDialog.showModal();
  }

  function moveLargePhoto(offset) {
    if (!photoViewerDialog?.open) return;
    renderLargePhoto(currentPhotoIndex + offset);
  }

  document.querySelector('[data-confirm-photo-protection]')?.addEventListener('click', () => {
    sessionStorage.setItem(protectionKey, 'yes');
    window.setTimeout(() => showLargePhoto(pendingPhotoIndex), 0);
  });
  document.querySelector('[data-close-photo-viewer]')?.addEventListener('click', () => photoViewerDialog?.close());
  previousPhotoButton?.addEventListener('click', () => { moveLargePhoto(-1); revealPreviewControls(); });
  nextPhotoButton?.addEventListener('click', () => { moveLargePhoto(1); revealPreviewControls(); });
  largeFavouriteButton?.addEventListener('click', async () => {
    const photo = galleryPhotos()[currentPhotoIndex];
    if (!photo?.id) return;
    const alreadyActive = favourites.has(String(photo.id));
    try {
      await toggleFavourite(photo.id);
      renderLargePhoto(currentPhotoIndex);
      if (favouriteToken && !alreadyActive && favourites.has(String(photo.id))) {
        bouncePreviewAction(largeFavouriteButton, 'preview-heart-pop');
      }
    } catch (error) {
      window.alert(error.message);
    }
  });
  largeAddButton?.addEventListener('click', () => {
    const photo = galleryPhotos()[currentPhotoIndex];
    if (!photo?.id) return;
    const alreadyAdded = basket.some(item => Number(item.photo_id) === Number(photo.id));
    addToBasket(photo.id);
    renderLargePhoto(currentPhotoIndex);
    if (!alreadyAdded) {
      bouncePreviewAction(largeAddButton, 'preview-action-success');
      bounceBasketIndicators();
    }
  });
  photoViewerDialog?.addEventListener('click', event => {
    if (event.target === photoViewerDialog) photoViewerDialog.close();
  });
  photoViewerImage?.addEventListener('dragstart', event => event.preventDefault());
  photoViewerStage?.addEventListener('pointerdown', revealPreviewControls);
  photoViewerStage?.addEventListener('touchstart', event => {
    revealPreviewControls();
    if (event.touches.length === 1) swipeStartX = event.touches[0].clientX;
  }, {passive: true});
  photoViewerStage?.addEventListener('touchmove', event => {
    if (event.touches && event.touches.length > 1) event.preventDefault();
  }, {passive: false});
  photoViewerStage?.addEventListener('touchend', event => {
    if (swipeStartX === null || !event.changedTouches.length) return;
    const distance = event.changedTouches[0].clientX - swipeStartX;
    swipeStartX = null;
    if (Math.abs(distance) < 45) return;
    moveLargePhoto(distance < 0 ? 1 : -1);
    revealPreviewControls();
  }, {passive: true});
  document.addEventListener('keydown', event => {
    if (!photoViewerDialog?.open) return;
    if (event.key === 'ArrowLeft') moveLargePhoto(-1);
    if (event.key === 'ArrowRight') moveLargePhoto(1);
    if (event.key === 'Escape') photoViewerDialog.close();
  });
  document.addEventListener('gesturestart', event => {
    if (photoViewerDialog?.open) event.preventDefault();
  }, {passive: false});

  function wire(card) {
    const id = card.dataset.photoId;
    const button = card.querySelector('.favourite');
    if (button) {
      setFavourite(button, id);
      button.addEventListener('click', () => toggleFavourite(id).catch(error => window.alert(error.message)));
    }
    const add = card.querySelector('.add-basket');
    if (add) add.addEventListener('click', () => addToBasket(id, add));
    const image = card.querySelector('img');
    image?.addEventListener('dragstart', event => event.preventDefault());
    image?.addEventListener('click', () => requestLargePhoto(galleryPhotos().findIndex(photo => String(photo.id) === String(id))));
    image?.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); requestLargePhoto(galleryPhotos().findIndex(photo => String(photo.id) === String(id))); }
    });
    if (image) { image.tabIndex = 0; image.setAttribute('role', 'button'); image.setAttribute('aria-label', `${image.alt || 'Event photograph'} — open larger preview`); }
  }

  document.querySelectorAll('.viewer-photo').forEach(wire);
  document.querySelector('[data-add-all-favourites]')?.addEventListener('click', () => {
    document.querySelectorAll('.viewer-photo[data-photo-id]').forEach(card => addToBasket(card.dataset.photoId));
    window.location.assign(`/view/${encodeURIComponent(slug)}/basket`);
  });
  document.querySelector('[data-reopen-favourites-form]')?.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const data = await openSession(new FormData(event.currentTarget).get('email'));
      const returnTo = page.dataset.favouritesReturnTo || sessionStorage.getItem(returnKey) || `/view/${encodeURIComponent(slug)}`;
      const target = new URL(data.url, window.location.origin);
      target.searchParams.set('return_to', returnTo);
      window.location.assign(`${target.pathname}${target.search}`);
    } catch (error) {
      const target = event.currentTarget.querySelector('[data-favourites-error]');
      if (target) { target.textContent = error.message; target.hidden = false; }
    }
  });

  const searchInput = document.getElementById('photo-search');
  searchInput?.addEventListener('search', () => {
    if (searchInput.value !== '') return;
    const params = new URLSearchParams(); if (currentFolder) params.set('folder', currentFolder);
    window.location.assign(`/view/${encodeURIComponent(slug)}${params.toString() ? `?${params}` : ''}`);
  });

  if (grid && status && !page.hasAttribute('data-favourites-page')) {
    let lastId = Math.max(0, ...Array.from(grid.querySelectorAll('[data-photo-id]')).map(el => Number(el.dataset.photoId)));
    const escapeHtml = value => String(value || '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
    function append(photo) {
      if (grid.querySelector(`[data-photo-id="${photo.id}"]`)) return;
      const card = document.createElement('article');
      card.className = `viewer-photo viewer-photo-new ${Number(photo.height) > Number(photo.width) ? 'portrait' : 'landscape'}`;
      card.dataset.photoId = photo.id;
      const dancer = escapeHtml(photo.dancer_numbers || '');
      card.innerHTML = `<img src="${escapeHtml(photo.cache_url)}" alt="Event photograph" loading="lazy" draggable="false"><div><span><strong>${escapeHtml(photo.code || '')}</strong><small>${escapeHtml(photo.filename || '')}${dancer ? ` · Auto-read ${dancer}` : ''}</small></span><span class="photo-actions"><button type="button" class="favourite" aria-label="Save favourite">♡</button><button type="button" class="add-basket">Add</button></span></div>`;
      if (allowLivePhotoCards) {
        grid.appendChild(card);
        wire(card);
        if (empty) empty.hidden = true;
      }
      lastId = Math.max(lastId, photo.id);
    }
    async function refresh() {
      try {
        const params = new URLSearchParams({after_id: String(lastId)}); if (currentFolder) params.set('folder', currentFolder); if (query) params.set('q', query);
        const response = await fetch(`/view/${encodeURIComponent(slug)}/updates?${params}`, {cache: 'no-store'});
        if (!response.ok) throw new Error(); const data = await response.json(); data.photos.forEach(append);
        status.textContent = data.photos.length ? `${data.photos.length} new` : 'Live';
      } catch (_) { status.textContent = 'Reconnecting…'; }
    }
    refresh(); setInterval(refresh, 2500);
  }
  page.addEventListener('contextmenu', event => event.preventDefault());
})();
