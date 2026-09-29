(() => {
  const page = document.querySelector('[data-live-dashboard]');
  if (!page) return;
  const text = (selector, value) => {
    const node = page.querySelector(selector);
    if (node) node.textContent = value;
  };
  async function refresh() {
    try {
      const response = await fetch('/developer/stats', {cache: 'no-store'});
      if (!response.ok) throw new Error('stats unavailable');
      const data = await response.json();
      text('[data-stat="monitored_events"]', data.monitored_events);
      text('[data-stat="queue_size"]', data.queue_size);
      text('[data-stat="workers"]', `${data.active_workers} active / ${data.worker_threads}`);
      text('[data-stat="rate"]', `${data.processed_last_minute} photos/min`);
      text('[data-stat="average"]', data.average_processing_ms ? `${data.average_processing_ms} ms` : '—');
      const last = data.last_processed;
      text('[data-stat="last_processed"]', last ? `Last: ${last.internal_code} at ${last.processed_at}` : 'No photographs processed yet.');
      for (const [key, value] of Object.entries(data.processing || {})) text(`[data-processing="${key}"]`, value);
    } catch (_) {
      text('[data-stat="workers"]', 'Reconnecting…');
    }
  }
  refresh();
  setInterval(refresh, 2000);
})();
