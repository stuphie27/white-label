(() => {
  const root = document.querySelector('[data-customer-search]');
  if (!root) return;
  const form = root.querySelector('[data-search-form]');
  const input = root.querySelector('[data-search-input]');
  const eventSelect = root.querySelector('[data-event-select]');
  const folderSelect = root.querySelector('[data-folder-select]');
  const results = root.querySelector('[data-results]');
  const status = root.querySelector('[data-search-status]');
  const empty = root.querySelector('[data-empty]');
  const suggestions = root.querySelector('[data-suggestions]');
  if (!form || !input || !eventSelect || !folderSelect || !results || !status) return;

  let timer = null;
  let controller = null;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

  function photoCard(photo) {
    const kind = photo.portrait ? ' portrait' : '';
    return `<article class="customer-search-photo${kind}">
      <img src="${escapeHtml(photo.image_url)}" alt="Photograph matching dancer ${escapeHtml(photo.dancer_numbers)}" loading="lazy">
      <div class="customer-search-photo-copy">
        <div><strong>Dancer ${escapeHtml(photo.dancer_numbers)}</strong><small>${escapeHtml(photo.folder || 'Unfiled')}</small><small>${escapeHtml(photo.code)}</small></div>
        <a class="button secondary" href="${escapeHtml(photo.gallery_url)}">Open gallery</a>
      </div>
    </article>`;
  }

  function renderSuggestions(items) {
    suggestions.innerHTML = '';
    if (!items || items.length < 2) {
      suggestions.hidden = true;
      return;
    }
    suggestions.innerHTML = '<span>Suggestions:</span>' + items.map((item) => `<button type="button" data-number="${escapeHtml(item)}">${escapeHtml(item)}</button>`).join('');
    suggestions.hidden = false;
  }

  async function search() {
    const q = input.value.replace(/\D/g, '').slice(0, 4);
    input.value = q;
    if (!q) {
      if (controller) controller.abort();
      results.innerHTML = '';
      status.textContent = 'Enter a dancer number to begin.';
      empty.hidden = true;
      suggestions.hidden = true;
      return;
    }
    if (controller) controller.abort();
    controller = new AbortController();
    status.textContent = `Searching for dancer ${q}…`;
    const params = new URLSearchParams({event_id: eventSelect.value, q, folder: folderSelect.value});
    try {
      const response = await fetch(`/api/customer-search?${params}`, {signal: controller.signal, headers: {'Accept': 'application/json'}});
      if (!response.ok) throw new Error('Search failed');
      const data = await response.json();
      results.innerHTML = data.photos.map(photoCard).join('');
      status.innerHTML = `<strong>${data.count}</strong> photograph${data.count === 1 ? '' : 's'} found for dancer ${escapeHtml(data.query)}`;
      empty.hidden = data.count !== 0;
      renderSuggestions(data.suggestions);
      const url = new URL(window.location.href);
      url.searchParams.set('event_id', eventSelect.value);
      url.searchParams.set('q', q);
      if (folderSelect.value) url.searchParams.set('folder', folderSelect.value); else url.searchParams.delete('folder');
      history.replaceState({}, '', url);
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = 'Search could not be completed. Please try again.';
    }
  }

  function queueSearch() {
    clearTimeout(timer);
    timer = setTimeout(search, 250);
  }

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      suggestions.hidden = true;
      input.select();
    }
  });
  input.addEventListener('input', queueSearch);
  folderSelect.addEventListener('change', search);
  eventSelect.addEventListener('change', () => {
    const url = new URL('/customer-search', window.location.origin);
    url.searchParams.set('event_id', eventSelect.value);
    window.location.assign(url);
  });
  form.addEventListener('submit', (event) => { event.preventDefault(); search(); });
  suggestions.addEventListener('click', (event) => {
    const button = event.target.closest('[data-number]');
    if (!button) return;
    input.value = button.dataset.number;
    search();
  });
})();
