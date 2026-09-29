(function(){
  'use strict';
  const bar=document.querySelector('[data-customer-welcome]');
  if(!bar)return;
  const slug=(bar.dataset.customerSlug||document.querySelector('[data-customer-slug]')?.dataset.customerSlug||document.querySelector('[data-viewer-slug]')?.dataset.viewerSlug||'').trim();
  const nameEl=bar.querySelector('[data-customer-welcome-name]');
  function safeName(value){return String(value||'').trim().replace(/\s+/g,' ').slice(0,80);}
  function render(value){const name=safeName(value);if(!name){bar.hidden=true;return;}if(nameEl)nameEl.textContent=name;bar.hidden=false;document.body.classList.add('has-customer-welcome');}
  function stored(){if(!slug)return '';return safeName(localStorage.getItem(`pirouette-customer-name:${slug}`)||'');}
  render(stored());
  window.addEventListener('storage',e=>{if(slug&&e.key===`pirouette-customer-name:${slug}`)render(e.newValue);});
  window.addEventListener('pirouette:customer-name',e=>render(e.detail&&e.detail.name));
})();
