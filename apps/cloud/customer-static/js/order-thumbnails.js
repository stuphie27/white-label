(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'order-image-dialog';
  dialog.innerHTML = '<div class="order-image-dialog-card"><button type="button" class="order-image-dialog-close" aria-label="Close expanded photograph">×</button><img alt="Expanded order photograph"></div>';
  document.body.appendChild(dialog);
  const image = dialog.querySelector('img');
  const close = () => { if (dialog.open) dialog.close(); };
  dialog.querySelector('.order-image-dialog-close').addEventListener('click', close);
  dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-order-thumbnail]');
    if (!trigger) return;
    event.preventDefault();
    image.src = trigger.dataset.orderThumbnail;
    image.alt = trigger.dataset.orderThumbnailAlt || 'Expanded order photograph';
    dialog.showModal();
  });
})();
