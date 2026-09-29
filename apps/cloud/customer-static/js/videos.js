(()=>{
 const page=document.querySelector('[data-viewer-slug]'); if(!page)return;
 const slug=page.dataset.viewerSlug,key=`pirouette-basket:${slug}`,tokenKey=`pirouette-favourites-token:${slug}`,emailKey=`pirouette-favourites-email:${slug}`;
 const dialog=document.getElementById('video-viewer-dialog'),player=dialog?.querySelector('[data-video-player]');let current=null;
 const money=p=>`£${(Number(p)/100).toFixed(2)}`;
 function items(){try{return JSON.parse(localStorage.getItem(key)||'[]')}catch(_){return[]}}
 function save(list){const count=list.reduce((n,i)=>n+Math.max(1,Number(i.quantity)||1),0);localStorage.setItem(key,JSON.stringify(list));document.querySelectorAll('.basket-count,[data-basket-count]').forEach(x=>x.textContent=String(count));window.dispatchEvent(new CustomEvent('pirouette:basket-count',{detail:{slug,count}}))}
 function add(id){const tile=document.querySelector(`[data-video-id="${id}"]`);if(!tile)return;const list=items();if(!list.some(i=>Number(i.video_id)===Number(id)))list.push({media_type:'video',video_id:Number(id),product_code:'video_download',quantity:1,price_pence:2300,name:tile.dataset.videoName});save(list)}
 async function ensureToken(videoId){let token=localStorage.getItem(tokenKey)||page.dataset.favouritesToken||'';if(token)return token;document.dispatchEvent(new CustomEvent('pirouette:first-favourite',{detail:{mediaType:'video',mediaId:videoId}}));return''}
 async function favourite(id,button){try{const token=await ensureToken(id);if(!token)return;const active=button?.getAttribute('aria-pressed')!=='true';const r=await fetch(`/api/view/${encodeURIComponent(slug)}/favourites/${encodeURIComponent(token)}/video/${id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active})});if(!r.ok)throw new Error('Unable to save favourite');document.querySelectorAll(`[data-favourite-video="${id}"]`).forEach(b=>{if(b.hasAttribute('data-unfavourite-button')){b.innerHTML=active?'<span aria-hidden="true">👎</span><span class="remove-label">Remove</span>':'<span aria-hidden="true">♡</span><span class="remove-label">Favourite</span>'}else{b.textContent=active?'♥':'♡'}b.setAttribute('aria-pressed',active?'true':'false');b.setAttribute('aria-label',active?'Remove video from favourites':'Save video as a favourite');if(!active&&page.hasAttribute('data-favourites-page'))b.closest('[data-video-id]')?.remove()});window.PirouetteFavouriteCount?.change(active?1:-1);const modal=dialog?.querySelector('[data-video-favourite-current]');if(modal&&Number(current)===Number(id)){modal.textContent=active?'♥ Favourited':'♡ Favourite';modal.setAttribute('aria-pressed',active?'true':'false')}}catch(e){window.alert(e.message)}}
 document.addEventListener('pirouette:favourite-created',e=>{const d=e.detail||{};if(d.type!=='video')return;window.PirouetteFavouriteCount?.change(1);document.querySelectorAll(`[data-favourite-video="${d.id}"]`).forEach(b=>{b.textContent='♥';b.setAttribute('aria-pressed','true')});});
 document.querySelectorAll('[data-add-video]').forEach(b=>b.addEventListener('click',()=>{add(b.dataset.addVideo);b.textContent='Added'}));
 document.querySelectorAll('[data-favourite-video]').forEach(b=>{
  // Items rendered on the favourites page are already saved, even when the
  // button contains descriptive text such as "Unfavourite".
  const alreadySaved=page.hasAttribute('data-favourites-page')||b.hasAttribute('data-unfavourite-button')||b.textContent.trim()==='♥';
  b.setAttribute('aria-pressed',alreadySaved?'true':'false');
  b.setAttribute('aria-label',alreadySaved?'Remove video from favourites':'Save video as a favourite');
  b.addEventListener('click',()=>favourite(b.dataset.favouriteVideo,b));
 });
 document.querySelectorAll('[data-video-cover-image]').forEach(img=>{
  const apply=()=>{const tile=img.closest('.video-tile');if(!tile)return;tile.classList.remove('video-orientation-pending','video-portrait','video-landscape','video-square');const ratio=(img.naturalWidth||1)/(img.naturalHeight||1);tile.classList.add(ratio<.85?'video-portrait':ratio>1.15?'video-landscape':'video-square')};
  if(img.complete) apply(); else img.addEventListener('load',apply,{once:true});
});
 document.querySelectorAll('[data-open-video]').forEach(b=>b.addEventListener('click',async()=>{
  current=b.dataset.openVideo;const tile=b.closest('[data-video-id]');
  dialog.querySelector('[data-video-title]').textContent=tile.dataset.videoName;
  dialog.querySelector('[data-video-price]').textContent='£23.00';
  player.src=`/videos/${current}/play`;
  dialog.showModal();
  try{await player.play()}catch(_){player.controls=true}
 }));
 dialog?.querySelector('[data-close-video]')?.addEventListener('click',()=>{player.pause();player.removeAttribute('src');player.load();dialog.close()});
 dialog?.querySelector('[data-video-add-current]')?.addEventListener('click',e=>{if(current){add(current);e.currentTarget.textContent='Added to basket'}});
 dialog?.querySelector('[data-video-favourite-current]')?.addEventListener('click',e=>{if(current)favourite(current,e.currentTarget)});
 dialog?.addEventListener('cancel',()=>{player.pause();player.removeAttribute('src');player.load()});
})();
