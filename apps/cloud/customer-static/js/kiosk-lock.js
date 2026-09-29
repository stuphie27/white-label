(() => {
  const root = document.querySelector('[data-kiosk-attract], [data-customer-session]');
  if (!root) return;

  document.documentElement.classList.add('kiosk-device-mode');

  // Keep the customer experience app-like and prevent accidental browser gestures.
  document.addEventListener('contextmenu', event => event.preventDefault());
  document.addEventListener('dragstart', event => event.preventDefault());
  document.addEventListener('selectstart', event => {
    if (!event.target.closest('input, textarea')) event.preventDefault();
  });

  let lastTouchEnd = 0;
  document.addEventListener('touchend', event => {
    const now = Date.now();
    if (now - lastTouchEnd <= 300) event.preventDefault();
    lastTouchEnd = now;
  }, { passive: false });

  // Hidden staff exit: tap the main logo five times, then enter the staff PIN.
  const logo = document.querySelector('.kiosk-logo');
  const dialog = document.getElementById('kiosk-staff-unlock-dialog');
  const form = document.querySelector('[data-kiosk-unlock-form]');
  const error = document.querySelector('[data-kiosk-unlock-error]');
  let taps = [];

  logo?.addEventListener('click', () => {
    const now = Date.now();
    taps = taps.filter(value => now - value < 2500);
    taps.push(now);
    if (taps.length >= 5) {
      taps = [];
      error.hidden = true;
      dialog?.showModal();
      setTimeout(() => form?.querySelector('input[name="pin"]')?.focus(), 50);
    }
  });

  document.querySelector('[data-kiosk-unlock-close]')?.addEventListener('click', () => dialog?.close());

  form?.addEventListener('submit', async event => {
    event.preventDefault();
    error.hidden = true;
    const data = new FormData(form);
    try {
      const response = await fetch('/api/kiosk/staff-unlock', {
        method: 'POST',
        cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pin: String(data.get('pin') || '')})
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'Incorrect staff PIN');
      location.replace('/');
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      form.querySelector('input[name="pin"]')?.select();
    }
  });

  // Register PWA support when available.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  }
})();
