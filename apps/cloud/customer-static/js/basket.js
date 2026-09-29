(()=>{
  const page=document.querySelector('[data-basket-slug]');
  if(!page)return;
  const slug=page.dataset.basketSlug;
  const key=`pirouette-basket:${slug}`;
  const FAVOURITES_EXTENSION_PENCE=1500;
  const favouritesRetentionKey=`pirouette-favourites-retention:${slug}`;
  const items=JSON.parse(localStorage.getItem(key)||'[]');
  const products=window.PIR_PRODUCTS||{};
  const videos=window.PIR_VIDEOS||{};
  const box=document.getElementById('basket-items');
  const empty=document.getElementById('basket-empty');
  const total=document.getElementById('basket-total');
  const summary=document.getElementById('pricing-summary');
  const catalogue=document.getElementById('shop-catalogue');
  const checkoutMessage=document.getElementById('checkout-message');
  const paymentMethod=document.getElementById('payment-method');
  const privacyConfirmed=document.getElementById('privacy-confirmed');
  const sumupDialog=document.getElementById('sumup-secure-dialog');
  const sumupStatus=document.getElementById('sumup-secure-status');
  const sumupTitle=document.getElementById('sumup-secure-title');
  const sumupCopy=document.getElementById('sumup-secure-copy');
  const reopenSumup=document.getElementById('reopen-sumup-payment');
  const cancelSumup=document.getElementById('cancel-sumup-payment');
  const finishOnlineSession=document.getElementById('finish-online-session');
  const orderReceivedDialog=document.getElementById('order-received-dialog');
  const orderReceivedNumber=document.getElementById('order-received-number');
  const orderReceivedCopy=document.getElementById('order-received-copy');
  const orderReceivedInstruction=document.getElementById('order-received-instruction');
  const orderReceivedPrintNote=document.getElementById('order-received-print-note');
  const closeCustomerSession=document.getElementById('close-customer-session');
  const paymentButtons=[...document.querySelectorAll('[data-payment-submit]')];
  const deliveryPanel=document.getElementById('checkout-delivery');
  const deliveryAddress=document.getElementById('checkout-delivery-address');
  const deliveryMethodInputs=[...document.querySelectorAll('input[name="delivery_method"]')];
  const POSTAGE_PENCE=700;
  let sumupWindow=null,sumupCheckoutUrl='',sumupOrderToken='',sumupWatch=null,paymentInProgress=false;
  let currentGrand=0;
  let currentQualifyingGrand=0;
  let loyaltyUse=false;
  let loyaltyState={points:0,redeem_points:0,reward_pence:0};
  const DOUBLE_KEYRING='gift_double_keyring';
  const SINGLE_KEYRING='gift_large_keyring';
  const money=p=>`£${(p/100).toFixed(2)}`;
  const photoUrl=id=>`/photos/${encodeURIComponent(id)}/cache`;
  const photoDialog=document.getElementById('checkout-photo-dialog');
  const photoPreview=document.getElementById('checkout-photo-preview');
  const photoCaption=document.getElementById('checkout-photo-caption');
  const photoClose=document.getElementById('checkout-photo-close');
  const photoNumber=document.getElementById('checkout-photo-number');
  const photoFolderLink=document.getElementById('checkout-photo-folder-link');
  async function openPhotoPreview(id,label){
    if(!photoDialog||!photoPreview)return;
    photoPreview.src=photoUrl(id);photoCaption.textContent=label||`Photo ${id}`;
    if(photoNumber)photoNumber.textContent='';if(photoFolderLink){photoFolderLink.hidden=true;photoFolderLink.removeAttribute('href');}
    if(typeof photoDialog.showModal==='function'&&!photoDialog.open)photoDialog.showModal();else photoDialog.setAttribute('open','');
    try{
      const response=await fetch(`/photos/${encodeURIComponent(id)}/checkout-info`,{cache:'no-store'});
      const info=await response.json();if(!response.ok)return;
      if(photoNumber)photoNumber.textContent=`Image ${info.number}`;
      if(photoFolderLink&&info.folder_url){photoFolderLink.textContent=`Photos › ${info.folder}`;photoFolderLink.href=info.folder_url;photoFolderLink.hidden=false;}
    }catch(_){}
  }
  function closePhotoPreview(){if(!photoDialog)return;if(typeof photoDialog.close==='function'&&photoDialog.open)photoDialog.close();else photoDialog.removeAttribute('open')}
  photoClose?.addEventListener('click',closePhotoPreview);
  photoDialog?.addEventListener('click',event=>{if(event.target===photoDialog)closePhotoPreview()});

  function bestPrice(quantity,p){
    const choices=[{q:1,price:p.price_pence,label:'Single'}].concat((p.offers||[]).filter(o=>o.active).map(o=>({q:o.quantity,price:o.price_pence,label:`${o.quantity} multibuy`})));
    const costs=Array(quantity+1).fill(Infinity),plans=Array.from({length:quantity+1},()=>[]);
    costs[0]=0;
    for(let q=1;q<=quantity;q++)for(const c of choices)if(c.q<=q&&costs[q-c.q]+c.price<costs[q]){costs[q]=costs[q-c.q]+c.price;plans[q]=plans[q-c.q].concat(c)}
    return {total:costs[quantity],plan:plans[quantity]};
  }

  function drawCatalogue(filter='all'){
    catalogue.innerHTML='';
    const shown=Object.entries(products).filter(([,p])=>filter==='all'||p.quick_checkout);
    const groups={};
    shown.forEach(([code,p])=>(groups[p.category]??=[]).push([code,p]));
    Object.entries(groups).forEach(([cat,group])=>{
      const section=document.createElement('section');section.className='catalogue-group';section.innerHTML=`<h2>${cat}</h2>`;
      const grid=document.createElement('div');grid.className='catalogue-grid';
      group.forEach(([code,p])=>{
        const card=document.createElement('button');card.type='button';card.className='catalogue-card';
        const offers=(p.offers||[]).filter(o=>o.active).map(o=>`${o.quantity} for ${money(o.price_pence)}`).join(' · ');
        card.innerHTML=`<strong>${p.name}</strong><span>${money(p.price_pence)}</span>${offers?`<small>${offers}</small>`:''}`;
        if(p.sold_out){
          card.disabled=true;
          card.classList.add('is-sold-out');
          card.setAttribute('aria-label',`${p.name} is sold out`);
        } else {
          // Product catalogue is informational only.
          // Customers choose a product separately for each photograph below.
          card.onclick=null;
          card.setAttribute('aria-label',`${p.name} — ${money(p.price_pence)}`);
        }
        grid.append(card);
      });
      section.append(grid);catalogue.append(section);
    });
    if(!shown.length)catalogue.innerHTML='<p>No products are available.</p>';
  }

  function secondPhotoControl(item,index){
    const wrap=document.createElement('label');
    wrap.className='second-photo-picker';
    wrap.innerHTML='<span>Photo for side 2</span>';
    const select=document.createElement('select');
    select.setAttribute('aria-label','Choose a different photograph for side two');
    const candidates=[...new Set(items.map(i=>Number(i.photo_id)))].filter(id=>id!==Number(item.photo_id));
    const prompt=document.createElement('option');
    prompt.value='';
    prompt.textContent=candidates.length?'Choose a different photo':'Add another photo to your basket first';
    select.append(prompt);
    candidates.forEach(id=>{
      const option=document.createElement('option');option.value=String(id);option.textContent=`Photo ${id}`;option.selected=Number(item.secondary_photo_id)===id;select.append(option);
    });
    select.onchange=()=>{item.secondary_photo_id=select.value?Number(select.value):null;save()};
    wrap.append(select);
    if(item.secondary_photo_id){
      const preview=document.createElement('div');preview.className='secondary-photo-preview';
      preview.innerHTML=`<img src="${photoUrl(item.secondary_photo_id)}" alt="Second photograph for the reverse side"><small>Side 2 · Photo ${item.secondary_photo_id}</small>`;
      wrap.append(preview);
    }
    const help=document.createElement('small');
    help.className='product-rule';
    help.textContent='Double-sided keyrings require two different photographs.';
    wrap.append(help);
    return wrap;
  }

  function updateBasketBadges(){const extensionCount=localStorage.getItem(favouritesRetentionKey)==='vault_year'?1:0;const count=items.reduce((n,i)=>n+Math.max(1,Number(i.quantity)||1),0)+extensionCount;document.querySelectorAll('.basket-count,[data-basket-count]').forEach(x=>x.textContent=String(count));window.dispatchEvent(new CustomEvent('pirouette:basket-count',{detail:{slug,count}}))}

  function render(){
    updateBasketBadges();
    box.innerHTML='';let standard=0,grand=0;const quantities={};
    items.filter(i=>!i.video_id).forEach(i=>quantities[i.product_code]=(quantities[i.product_code]||0)+(i.quantity||1));
    items.filter(i=>i.video_id).forEach(i=>{const v=videos[String(i.video_id)];if(v){standard+=Number(v.price_pence);grand+=Number(v.price_pence)}});
    Object.entries(quantities).forEach(([code,q])=>{const p=products[code];if(p){standard+=q*p.price_pence;grand+=bestPrice(q,p).total}});
    items.forEach((item,index)=>{
      if(item.video_id){
        const v=videos[String(item.video_id)]||{filename:item.name||`Video ${item.video_id}`,price_pence:item.price_pence||0,duration_seconds:0};
        const row=document.createElement('article');row.className='basket-row basket-video-row';
        const media=document.createElement('div');media.className='basket-photo';media.style.cursor='default';media.innerHTML=`<img src="/videos/${encodeURIComponent(item.video_id)}/cover" alt="Video cover"><span><strong>${v.filename}</strong><small>Video download · ${Math.round(Number(v.duration_seconds)||0)} seconds</small></span>`;
        const controls=document.createElement('div');controls.className='basket-product-controls';controls.innerHTML=`<strong>Video download — ${money(Number(v.price_pence)||0)}</strong><small>Fixed event video price</small>`;
        const qty=document.createElement('span');qty.textContent='1';
        const actions=document.createElement('div');actions.className='basket-row-actions';const remove=document.createElement('button');remove.type='button';remove.className='button button-small button-secondary';remove.textContent='Remove';remove.onclick=()=>{items.splice(index,1);save()};actions.append(remove);
        row.append(media,controls,qty,actions);box.append(row);return;
      }
      if(item.product_code && !products[item.product_code])item.product_code='';
      const row=document.createElement('article');row.className='basket-row';
      const photo=document.createElement('div');photo.className='basket-photo';
      photo.innerHTML=`<img src="${photoUrl(item.photo_id)}" alt="Photograph ${item.photo_id} in your basket"><span><strong>Photo ${item.photo_id}</strong><small>Tap to enlarge</small></span>`;
      photo.tabIndex=0;photo.setAttribute('role','button');photo.setAttribute('aria-label',`Enlarge photograph ${item.photo_id}`);photo.onclick=()=>openPhotoPreview(item.photo_id,`Photo ${item.photo_id}`);photo.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openPhotoPreview(item.photo_id,`Photo ${item.photo_id}`)}};
      const controls=document.createElement('div');
      controls.className='basket-product-controls';

      const question=document.createElement('strong');
      question.className='basket-product-question';
      question.textContent='How would you like this photograph?';
      controls.append(question);

      const select=document.createElement('select');

      const chooseOption=document.createElement('option');
      chooseOption.value='';
      chooseOption.textContent='Please choose a product…';
      chooseOption.selected=!item.product_code;
      chooseOption.disabled=false;
      select.appendChild(chooseOption);

      Object.entries(products).forEach(([code,p])=>{
        const o=document.createElement('option');
        o.value=code;
        o.textContent=`${p.name} — ${money(p.price_pence)}${p.sold_out?' — SOLD OUT':''}`;
        o.selected=item.product_code===code;
        o.disabled=Boolean(p.sold_out)&&!o.selected;
        select.appendChild(o)
      });
      select.onchange=()=>{item.product_code=select.value;if(select.value!==DOUBLE_KEYRING)delete item.secondary_photo_id;save()};
      controls.append(select);

      if(!item.product_code){
        const chooseWarning=document.createElement('small');
        chooseWarning.className='product-rule product-choice-required';
        chooseWarning.textContent='Choose one option for this photograph before continuing.';
        controls.append(chooseWarning);
      }

      if(products[item.product_code]?.sold_out){const warning=document.createElement('small');warning.className='sold-out-warning';warning.textContent='This product is sold out. Choose another product before checkout.';controls.append(warning)}
      if(item.product_code===DOUBLE_KEYRING)controls.append(secondPhotoControl(item,index));
      if(item.product_code===SINGLE_KEYRING){const note=document.createElement('small');note.className='product-rule';note.textContent='The same photograph is printed on both sides.';controls.append(note)}
      const qty=document.createElement('input');qty.type='number';qty.min='1';qty.max='50';qty.value=item.quantity||1;qty.setAttribute('aria-label','Quantity');qty.onchange=()=>{item.quantity=Math.max(1,Number(qty.value)||1);save()};
      const actions=document.createElement('div');actions.className='basket-row-actions';
      const addVersion=document.createElement('button');addVersion.type='button';addVersion.className='button button-small button-secondary';addVersion.textContent='Add another product';addVersion.setAttribute('aria-label',`Add another product using photograph ${item.photo_id}`);addVersion.onclick=()=>{
        const clone={photo_id:Number(item.photo_id),product_code:'',quantity:1};
        items.splice(index+1,0,clone);save();
        const rows=box.querySelectorAll('.basket-row');
        rows[index+1]?.querySelector('.basket-product-controls select')?.focus();
      };
      const remove=document.createElement('button');remove.type='button';remove.className='button button-small button-secondary';remove.textContent='Remove';remove.onclick=()=>{items.splice(index,1);save()};
      actions.append(addVersion,remove);
      row.append(photo,controls,qty,actions);box.append(row);
    });
    const favouritesExtensionActive=localStorage.getItem(favouritesRetentionKey)==='vault_year';
    if(favouritesExtensionActive){
      grand+=FAVOURITES_EXTENSION_PENCE;standard+=FAVOURITES_EXTENSION_PENCE;
      const extension=document.createElement('article');extension.className='basket-row basket-service-row favourites-extension-row';
      const media=document.createElement('div');media.className='basket-photo basket-service-icon';media.innerHTML='<span class="favourites-extension-heart" aria-hidden="true">♥</span><span><strong>35-day favourites extension</strong><small>Keep your saved favourites available online for 35 days</small></span>';
      const controls=document.createElement('div');controls.className='basket-product-controls';controls.innerHTML='<strong>Favourites extension</strong><small>Optional — remove this and your favourites return to the free 5-day period.</small>';
      const price=document.createElement('strong');price.className='basket-service-price';price.textContent=money(FAVOURITES_EXTENSION_PENCE);
      const actions=document.createElement('div');actions.className='basket-row-actions';const remove=document.createElement('button');remove.type='button';remove.className='button button-small button-secondary';remove.textContent='Remove extension';
      remove.onclick=async()=>{
        remove.disabled=true;
        try{
          const token=localStorage.getItem(`pirouette-favourites-token:${slug}`)||'';
          if(token){
            const response=await fetch(`/api/view/${encodeURIComponent(slug)}/favourites/${encodeURIComponent(token)}/retention`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({retention_choice:'temporary_link'})});
            const result=await response.json();if(!response.ok)throw new Error(result.detail||'Unable to remove the favourites extension');
          }
          localStorage.setItem(favouritesRetentionKey,'temporary_link');
          localStorage.removeItem(`pirouette-favourites-payment:${slug}`);
          checkoutMessage.textContent='35-day extension removed. Your favourites will stay on the normal free 5-day period.';
          render();
        }catch(error){remove.disabled=false;checkoutMessage.textContent=error.message||'Unable to remove the favourites extension.'}
      };
      actions.append(remove);extension.append(media,controls,price,actions);box.append(extension);
    }
    const hasPhysicalItems=items.some(item=>{
      if(item.video_id||!item.product_code)return false;
      const product=products[item.product_code];
      return Boolean(
        product &&
        String(product.fulfilment_type||'print')!=='digital'
      );
    });

    if(!hasPhysicalItems){
      const collection=document.querySelector(
        'input[name="delivery_method"][value="collection"]'
      );
      if(collection)collection.checked=true;
    }

    const selectedDelivery=hasPhysicalItems
      ? (
          document.querySelector(
            'input[name="delivery_method"]:checked'
          )?.value || 'collection'
        )
      : 'collection';

    const postageActive=
      hasPhysicalItems &&
      selectedDelivery==='postage';

    if(deliveryPanel){
      deliveryPanel.hidden=!hasPhysicalItems;
    }

    if(deliveryAddress){
      deliveryAddress.hidden=!postageActive;

      deliveryAddress.querySelectorAll('input').forEach(input=>{
        input.required=
          postageActive &&
          [
            'delivery_name',
            'delivery_address_line_1',
            'delivery_town_city',
            'delivery_postcode',
            'delivery_country'
          ].includes(input.name);
      });
    }

    const displayGrand=
      grand +
      (postageActive ? POSTAGE_PENCE : 0);

    currentGrand=grand;
    currentQualifyingGrand=Math.max(
      0,
      grand-(favouritesExtensionActive?FAVOURITES_EXTENSION_PENCE:0)
    );

    empty.hidden=
      items.length>0 ||
      favouritesExtensionActive;

    total.textContent=money(displayGrand);
    summary.innerHTML='';
    if(favouritesExtensionActive){const extension=document.createElement('div');extension.className='pricing-line';extension.innerHTML=`<span><strong>35-day favourites extension</strong><small>Optional basket item · paid with your photographs at checkout</small></span><strong>${money(FAVOURITES_EXTENSION_PENCE)}</strong>`;summary.append(extension);}
    Object.entries(quantities).forEach(([code,q])=>{const p=products[code];if(!p)return;const result=bestPrice(q,p),saving=q*p.price_pence-result.total;const line=document.createElement('div');line.className='pricing-line';line.innerHTML=`<span><strong>${q} × ${p.name}</strong>${saving?`<small>Multibuy saving ${money(saving)}</small>`:''}</span><strong>${money(result.total)}</strong>`;summary.append(line)});
    if(postageActive){
      const postageLine=document.createElement('div');
      postageLine.className='pricing-line';
      postageLine.innerHTML=`<span><strong>UK postage</strong><small>Flat rate · once per order</small></span><strong>${money(POSTAGE_PENCE)}</strong>`;
      summary.append(postageLine);
    }

    if(standard>grand){const saveLine=document.createElement('div');saveLine.className='pricing-saving';saveLine.innerHTML=`You save <strong>${money(standard-grand)}</strong>`;summary.append(saveLine)}
  }


  async function refreshLoyalty(){
    const form=document.getElementById('checkout-form');
    const panel=document.getElementById('checkout-loyalty');
    const balance=document.getElementById('checkout-loyalty-balance');
    const status=document.getElementById('checkout-loyalty-status');
    const actions=document.getElementById('checkout-loyalty-actions');
    const useButton=document.getElementById('use-loyalty-points');
    const saveButton=document.getElementById('save-loyalty-points');
    const available=document.getElementById('loyalty-points-available');
    const earned=document.getElementById('loyalty-points-earned');
    const after=document.getElementById('loyalty-points-after');
    if(!form||!panel||!balance||!status||!actions||!useButton||!saveButton||!available||!earned||!after)return;
    const email=String(form.elements.customer_email?.value||'').trim().toLowerCase();
    panel.hidden=false;
    if(!email||!form.elements.customer_email.checkValidity()){
      loyaltyUse=false;
      panel.classList.remove('is-using');
      available.textContent='—';earned.textContent='—';after.textContent='—';
      balance.textContent='Enter your email above to check your Sophie’s Rewards balance and see what you’ll earn from this order.';
      actions.hidden=true;
      status.textContent='100 points = £5 off. Save points for a future qualifying purchase.';
      return;
    }
    try{
      const response=await fetch(`/api/view/${encodeURIComponent(slug)}/loyalty?email=${encodeURIComponent(email)}`,{cache:'no-store'});
      const result=await response.json();
      if(!response.ok)throw new Error('Unable to check rewards.');
      loyaltyState=result||{points:0,redeem_points:0,reward_pence:0};
      const points=Number(loyaltyState.points)||0;
      const minimumOrderPence=Number(loyaltyState.minimum_order_pence)||2000;
      const minimumShortfallPence=Math.max(0,minimumOrderPence-currentQualifyingGrand);
      const maxBlocks=currentQualifyingGrand>=minimumOrderPence?Math.min(Math.floor(points/100),1,Math.floor((currentQualifyingGrand*0.5)/500)):0;
      loyaltyState.redeem_points=Math.max(0,Math.min(Number(loyaltyState.redeem_points)||0,maxBlocks*100));
      loyaltyState.reward_pence=loyaltyState.redeem_points/100*500;
      if(loyaltyState.reward_pence<=0)loyaltyUse=false;
      const selectedRewardPence=loyaltyUse?loyaltyState.reward_pence:0;
      const expectedPoints=Math.floor(Math.max(0,currentQualifyingGrand-selectedRewardPence)/100);
      const projectedPoints=Math.max(0,points-(loyaltyUse?loyaltyState.redeem_points:0)+expectedPoints);
      const pointsWord=expectedPoints===1?'point':'points';
      available.textContent=`${points} ${points===1?'point':'points'}`;
      earned.textContent=`+${expectedPoints} ${pointsWord}`;
      after.textContent=`${projectedPoints} ${projectedPoints===1?'point':'points'}`;
      // v1.8.8 regression marker: const canRedeem=loyaltyState.reward_pence>0;
      const canRedeem=Boolean(loyaltyState.redemption_eligible)&&loyaltyState.reward_pence>0&&currentQualifyingGrand>=minimumOrderPence;
      actions.hidden=!canRedeem;
      useButton.hidden=!canRedeem;
      saveButton.hidden=!canRedeem;
      useButton.setAttribute('aria-pressed',loyaltyUse?'true':'false');
      saveButton.setAttribute('aria-pressed',loyaltyUse?'false':'true');
      useButton.textContent=loyaltyUse?`Reward selected — ${money(loyaltyState.reward_pence)} off`:`Use ${loyaltyState.redeem_points} points — save ${money(loyaltyState.reward_pence)}`;
      saveButton.textContent=loyaltyUse?'Save points for later':'Points saved for later';
      if(canRedeem){
        balance.textContent=`You have ${points} points available. You can take ${money(loyaltyState.reward_pence)} off this order or keep all your points for another event.`;
      }else if(points>=100&&minimumShortfallPence>0){
        balance.textContent=`You have ${points} points available and a £5 reward ready. Add ${money(minimumShortfallPence)} more to qualifying items to use it at this competition, or save it for another competition.`;
      }else if(points>0){
        balance.textContent=`You have ${points} points available. You’ll earn ${expectedPoints} ${pointsWord} from this order once payment is confirmed.`;
      }else{
        balance.textContent=`You currently have 0 points. You’ll earn ${expectedPoints} ${pointsWord} from this order once payment is confirmed.`;
      }
      if(loyaltyUse&&canRedeem){
        panel.classList.add('is-using');
        status.textContent=`Reward selected: ${loyaltyState.redeem_points} points = ${money(loyaltyState.reward_pence)} off today. You’ll earn ${expectedPoints} new ${pointsWord} on the qualifying amount you pay. Your new balance after payment will be ${projectedPoints} points.`;
      }else{
        panel.classList.remove('is-using');
        if(projectedPoints>=100){
          status.textContent=`After payment you’ll have ${projectedPoints} points — enough for at least £5 off a qualifying £20+ order at a future participating competition.`;
        }else{
          const gap=Math.max(0,100-projectedPoints);
          status.textContent=`After payment you’ll have ${projectedPoints} points. ${gap} ${gap===1?'more point':'more points'} unlocks your first £5 reward.`;
        }
      }
    }catch(_){
      loyaltyUse=false;
      panel.classList.remove('is-using');
      available.textContent='Unavailable';earned.textContent='—';after.textContent='—';
      balance.textContent='Sophie’s Rewards is available at checkout, but we could not check this email address just now.';
      actions.hidden=true;
      status.textContent='You can continue with your order without using points.';
    }
  }

  function bindLoyalty(){
    const form=document.getElementById('checkout-form');
    const email=form?.elements.customer_email;
    let timer=null;
    const queue=()=>{clearTimeout(timer);timer=setTimeout(refreshLoyalty,350)};
    email?.addEventListener('input',()=>{loyaltyUse=false;queue()});
    email?.addEventListener('blur',refreshLoyalty);
    document.getElementById('use-loyalty-points')?.addEventListener('click',()=>{loyaltyUse=true;refreshLoyalty()});
    document.getElementById('save-loyalty-points')?.addEventListener('click',()=>{loyaltyUse=false;refreshLoyalty()});
    queue();
  }

  function prefillCustomerDetails(){
    const form=document.getElementById('checkout-form'); if(!form)return;
    const email=localStorage.getItem(`pirouette-customer-email:${slug}`)||localStorage.getItem(`pirouette-favourites-email:${slug}`)||'';
    const name=localStorage.getItem(`pirouette-customer-name:${slug}`)||'';
    const phone=localStorage.getItem(`pirouette-customer-phone:${slug}`)||'';
    if(name&&!form.elements.customer_name.value)form.elements.customer_name.value=name;
    if(email&&!form.elements.customer_email.value)form.elements.customer_email.value=email;
    if(phone&&!form.elements.customer_phone.value)form.elements.customer_phone.value=phone;
    const remember=()=>{
      localStorage.setItem(`pirouette-customer-name:${slug}`,form.elements.customer_name.value.trim());
      localStorage.setItem(`pirouette-customer-email:${slug}`,form.elements.customer_email.value.trim().toLowerCase());
      localStorage.setItem(`pirouette-customer-phone:${slug}`,form.elements.customer_phone.value.trim());
    };
    ['customer_name','customer_email','customer_phone'].forEach(field=>form.elements[field]?.addEventListener('input',remember));
  }


  async function saveBasketToFavourites(){
    const button=document.getElementById('save-basket-favourites');
    const status=document.getElementById('save-basket-favourites-status');
    const panel=document.getElementById('checkout-save-later');
    const form=document.getElementById('checkout-form');

    if(!button||!status||!form)return;

    const email=String(form.elements.customer_email?.value||'')
      .trim()
      .toLowerCase();

    if(!email){
      status.textContent='Please enter your email address above first.';
      form.elements.customer_email?.focus();
      return;
    }

    if(!form.elements.customer_email.checkValidity()){
      form.elements.customer_email.reportValidity();
      return;
    }

    const photoIds=[
      ...new Set(
        items.flatMap(item=>{
          if(item.video_id)return [];

          const ids=[];

          if(Number(item.photo_id))
            ids.push(Number(item.photo_id));

          if(Number(item.secondary_photo_id))
            ids.push(Number(item.secondary_photo_id));

          return ids;
        })
      )
    ];

    const videoIds=[
      ...new Set(
        items
          .filter(item=>Number(item.video_id))
          .map(item=>Number(item.video_id))
      )
    ];

    if(!photoIds.length&&!videoIds.length){
      status.textContent='There are no photographs or videos in your basket to save.';
      return;
    }

    button.disabled=true;
    status.textContent='Saving your favourites…';

    try{

      localStorage.setItem(
        `pirouette-customer-email:${slug}`,
        email
      );

      /*
       * Get or create the customer's existing favourites session.
       * The backend reuses the session for this event/email.
       */
      const sessionResponse=await fetch(
        `/api/view/${encodeURIComponent(slug)}/favourites/session`,
        {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            email:email
          })
        }
      );

      const session=await sessionResponse.json();

      if(!sessionResponse.ok)
        throw new Error(
          session.detail||
          'Unable to create your favourites folder.'
        );

      const token=String(session.token||'');

      if(!token)
        throw new Error(
          'Your favourites folder could not be opened.'
        );

      localStorage.setItem(
        `pirouette-favourites-token:${slug}`,
        token
      );

      localStorage.setItem(
        `pirouette-favourites-email:${slug}`,
        email
      );


      /*
       * Save every unique photograph in the basket.
       * This deliberately includes secondary photographs used
       * on double-sided keyrings.
       */
      for(const photoId of photoIds){

        const response=await fetch(
          `/api/view/${encodeURIComponent(slug)}/favourites/${encodeURIComponent(token)}/${encodeURIComponent(photoId)}`,
          {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({active:true})
          }
        );

        const result=await response.json();

        if(!response.ok)
          throw new Error(
            result.detail||
            `Unable to save photograph ${photoId}.`
          );
      }


      /*
       * Save videos using the existing video favourites endpoint.
       */
      for(const videoId of videoIds){

        const response=await fetch(
          `/api/view/${encodeURIComponent(slug)}/favourites/${encodeURIComponent(token)}/video/${encodeURIComponent(videoId)}`,
          {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({active:true})
          }
        );

        const result=await response.json();

        if(!response.ok)
          throw new Error(
            result.detail||
            `Unable to save video ${videoId}.`
          );
      }


      const count=photoIds.length+videoIds.length;

      localStorage.setItem(
        `pirouette-favourites-count:${slug}`,
        String(count)
      );

      panel?.classList.add('is-saved');

      status.textContent=
        `${count} ${count===1?'item has':'items have'} been saved to My Favourites.`;

      button.textContent='✓ Saved — Open My Favourites';

      /*
       * Give the customer a moment to see confirmation,
       * then take them directly to their saved favourites.
       */
      setTimeout(()=>{
        location.assign(
          `/view/${encodeURIComponent(slug)}/favourites?token=${encodeURIComponent(token)}`
        );
      },900);

    }catch(error){

      button.disabled=false;

      status.textContent=
        error.message||
        'We could not save your favourites. Please ask a member of staff for help.';
    }
  }

  function setPaymentBusy(busy){paymentInProgress=busy;paymentButtons.forEach(button=>button.disabled=busy)}
  async function endCustomerSession(){
    const sessionKey=`pirouette-customer-session:${slug}`;
    const token=localStorage.getItem(sessionKey);
    if(token){try{await fetch(`/api/view/${encodeURIComponent(slug)}/session/close`,{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})})}catch(_){}}
    for(let index=localStorage.length-1;index>=0;index--){const storedKey=localStorage.key(index);if(storedKey&&storedKey.startsWith('pirouette-'))localStorage.removeItem(storedKey)}
    sessionStorage.clear();
    if('caches' in window){try{await Promise.all((await caches.keys()).map(name=>caches.delete(name)))}catch(_){}}
    try{await fetch('/api/kiosk/end-session',{method:'POST',cache:'no-store',keepalive:true})}catch(_){}
    location.replace(`/kiosk/${encodeURIComponent(slug)}`);
  }
  function showOrderReceived(result,method){
    orderReceivedNumber.textContent=result.order_number?`Order ${result.order_number}`:'';
    orderReceivedCopy.textContent='Your order has been safely saved and is waiting for payment.';
    orderReceivedInstruction.innerHTML='<strong>Please finish your session and head over to the Sophie’s Photography Service Desk, where we can take payment by cash or card.</strong>';
    orderReceivedPrintNote.textContent='Your order will enter production once payment has been confirmed.';
    if(typeof orderReceivedDialog.showModal==='function'&&!orderReceivedDialog.open)orderReceivedDialog.showModal();else orderReceivedDialog.setAttribute('open','');
  }
  function showSumupDialog(){if(typeof sumupDialog.showModal==='function'&&!sumupDialog.open)sumupDialog.showModal();else sumupDialog.setAttribute('open','')}
  function hideSumupDialog(){if(sumupWatch){clearInterval(sumupWatch);sumupWatch=null}if(typeof sumupDialog.close==='function'&&sumupDialog.open)sumupDialog.close();else sumupDialog.removeAttribute('open')}
  async function checkSumupPayment(){
    if(!sumupOrderToken)return;
    try{
      const response=await fetch(`/api/order/${encodeURIComponent(sumupOrderToken)}/payment-status`,{cache:'no-store'});
      const result=await response.json();
      if(response.ok&&result.paid){
        if(sumupWatch){clearInterval(sumupWatch);sumupWatch=null}
        if(sumupWindow&&!sumupWindow.closed)sumupWindow.close();
        localStorage.removeItem(key);
        sumupDialog.classList.add('sumup-payment-success');
        document.querySelector('.sumup-secure-lock').textContent='✓';
        sumupTitle.textContent='Thank you';
        sumupCopy.textContent='Your secure SumUp payment has been received successfully.';
        sumupStatus.textContent='Your order is safely recorded. Finish this session to remove your customer details from this device.';
        finishOnlineSession.hidden=false;
        setPaymentBusy(false);
        // Stay inside Pirouette after successful payment.
        // The server has already independently confirmed the SumUp checkout
        // before result.paid can become true. Do not send the kiosk into the
        // generic order-status page: keep the customer in the controlled
        // Pirouette session and let them finish the session explicitly.

      }else if(response.ok){sumupStatus.textContent='Secure payment is still in progress…'}
    }catch(_){sumupStatus.textContent='Waiting for confirmation from SumUp…'}
  }
  function startSumupWatch(){if(sumupWatch)clearInterval(sumupWatch);checkSumupPayment();sumupWatch=setInterval(checkSumupPayment,1800)}
  function openSecureSumupWindow(){
    if(!sumupCheckoutUrl)return;
    sumupWindow=window.open(sumupCheckoutUrl,'pirouette-sumup-secure','popup=yes,width=520,height=760,resizable=no,scrollbars=yes,toolbar=no,menubar=no,location=no,status=no');
    if(sumupWindow){reopenSumup.hidden=true;sumupStatus.textContent='Complete payment in the secure SumUp window. It will close automatically after payment.';startSumupWatch()}
    else{reopenSumup.hidden=false;sumupStatus.textContent='The payment window was blocked. Tap below to open secure SumUp payment.'}
  }
  reopenSumup.addEventListener('click',openSecureSumupWindow);
  finishOnlineSession.addEventListener('click',endCustomerSession);
  closeCustomerSession.addEventListener('click',endCustomerSession);
  document.getElementById('checkout-end-session')?.addEventListener('click',endCustomerSession);
  cancelSumup.addEventListener('click',()=>{
    if(sumupWindow&&!sumupWindow.closed)sumupWindow.close();
    sumupWindow=null;
    sumupCheckoutUrl='';
    sumupOrderToken='';
    hideSumupDialog();
    sumupDialog.classList.remove('sumup-payment-success');
    const lock=document.querySelector('.sumup-secure-lock');
    if(lock)lock.textContent='🔒';
    sumupTitle.textContent='Secure SumUp Payment';
    sumupCopy.textContent='Complete your card payment securely with SumUp.';
    finishOnlineSession.hidden=true;
    reopenSumup.hidden=true;
    setPaymentBusy(false);
    checkoutMessage.textContent='Payment cancelled — nothing has been charged. Your basket is still here and you can try again or choose another payment method.';
  });
  async function submitWithPayment(method){
    if(paymentInProgress||(!items.length&&localStorage.getItem(favouritesRetentionKey)!=='vault_year'))return;
    const form=document.getElementById('checkout-form');paymentMethod.value=method;privacyConfirmed.value='yes';if(!form.reportValidity())return;
    const missingProduct=items.find(i=>!i.video_id&&!i.product_code);
    if(missingProduct){
      checkoutMessage.textContent='Please choose a product for each photograph before continuing.';
      const missingIndex=items.indexOf(missingProduct);
      const rows=box.querySelectorAll('.basket-row');
      const select=rows[missingIndex]?.querySelector('.basket-product-controls select');
      select?.scrollIntoView({behavior:'smooth',block:'center'});
      select?.focus();
      return;
    }

    const soldOut=items.find(i=>!i.video_id&&products[i.product_code]?.sold_out);
    if(soldOut){checkoutMessage.textContent=`${products[soldOut.product_code].name} is sold out. Choose another product before checkout.`;document.querySelector('.sold-out-warning')?.scrollIntoView({behavior:'smooth',block:'center'});return}
    const invalid=items.find(i=>!i.video_id&&i.product_code===DOUBLE_KEYRING&&(!i.secondary_photo_id||Number(i.secondary_photo_id)===Number(i.photo_id)));
    if(invalid){checkoutMessage.textContent='Choose a different second photograph for every double-sided keyring.';document.querySelector('.second-photo-picker select')?.focus();return}
    setPaymentBusy(true);checkoutMessage.textContent=method==='sumup_online'?'Opening secure SumUp payment…':'Creating your order…';
    let reservedWindow=null;if(method==='sumup_online')reservedWindow=window.open('about:blank','pirouette-sumup-secure','popup=yes,width=520,height=760,resizable=no,scrollbars=yes,toolbar=no,menubar=no,location=no,status=no');
    const f=new FormData(form);
    localStorage.setItem(`pirouette-customer-name:${slug}`,String(f.get('customer_name')||'').trim());
    localStorage.setItem(`pirouette-customer-email:${slug}`,String(f.get('customer_email')||'').trim().toLowerCase());
    localStorage.setItem(`pirouette-customer-phone:${slug}`,String(f.get('customer_phone')||'').trim());
    try{
      const selectedDelivery=String(
        f.get('delivery_method')||'collection'
      );

      const deliveryAddressPayload=
        selectedDelivery==='postage'
          ? {
              name:String(
                f.get('delivery_name')||''
              ).trim(),
              address_line_1:String(
                f.get('delivery_address_line_1')||''
              ).trim(),
              address_line_2:String(
                f.get('delivery_address_line_2')||''
              ).trim(),
              town_city:String(
                f.get('delivery_town_city')||''
              ).trim(),
              county:String(
                f.get('delivery_county')||''
              ).trim(),
              postcode:String(
                f.get('delivery_postcode')||''
              ).trim(),
              country:String(
                f.get('delivery_country')||''
              ).trim()
            }
          : null;

      const response=await fetch(
        `/api/view/${encodeURIComponent(slug)}/orders`,
        {
          method:'POST',
          headers:{
            'Content-Type':'application/json'
          },
          body:JSON.stringify({
            customer_name:f.get('customer_name'),
            customer_email:f.get('customer_email'),
            customer_phone:f.get('customer_phone'),
            payment_method:method,
            items,
            favourites_token:
              localStorage.getItem(
                `pirouette-favourites-token:${slug}`
              )||'',
            use_loyalty_points:loyaltyUse,
            delivery_method:selectedDelivery,
            delivery_address:deliveryAddressPayload
          })
        }
      );
      const result=await response.json();if(!response.ok)throw new Error(result.detail||'Could not create the order.');
      if(method==='sumup_online'&&result.payment==='online'&&result.checkout_url){
        sumupCheckoutUrl=result.checkout_url;sumupOrderToken=result.token;
        // SumUp Hosted Checkout must be a top-level page. It cannot be rendered
        // inside Pirouette's Staff Live Customer Preview iframe. On a real kiosk
        // (the normal iPad case), navigate the current page directly to SumUp.
        // When the customer UI is being viewed inside the staff preview iframe,
        // use the window reserved by the original customer tap instead.
        sumupWindow=reservedWindow;
        showSumupDialog();

        if(sumupWindow){
          try{
            sumupWindow.location.replace(sumupCheckoutUrl);
            try{sumupWindow.resizeTo(520,760)}catch(_){}
            sumupWindow.focus();
            reopenSumup.hidden=true;
            sumupStatus.textContent='Complete payment in the secure SumUp window. Sophie’s Photography does not receive or store your card details.';
            startSumupWatch();
          }
          catch(_){
            sumupWindow=null;
            openSecureSumupWindow();
          }
        }else{
          openSecureSumupWindow();
        }
        return;
      }
      if(reservedWindow&&!reservedWindow.closed)reservedWindow.close();if(method==='sumup_online')throw new Error(result.payment_error||'SumUp online payment is not available. Please choose cash or card at the Sophie’s Photography Stand.');
      localStorage.removeItem(key);location.assign(`/order/${encodeURIComponent(result.token)}`);
    }catch(error){if(reservedWindow&&!reservedWindow.closed)reservedWindow.close();setPaymentBusy(false);checkoutMessage.textContent=error.message||'Could not create the order.'}
  }
  document.getElementById('save-basket-favourites')?.addEventListener('click',saveBasketToFavourites);

  paymentButtons.forEach(button=>button.addEventListener('click',()=>submitWithPayment(button.dataset.paymentSubmit)));

  function save(){localStorage.setItem(key,JSON.stringify(items));checkoutMessage.textContent='';render()}
  document.querySelectorAll('.shop-tab').forEach(tab=>tab.onclick=()=>{document.querySelectorAll('.shop-tab').forEach(x=>x.classList.remove('active'));tab.classList.add('active');drawCatalogue(tab.dataset.shopFilter)});
  document.getElementById('checkout-form').addEventListener('submit',e=>e.preventDefault());

  deliveryMethodInputs.forEach(
    input=>input.addEventListener('change',render)
  );

  prefillCustomerDetails();
  bindLoyalty();
  drawCatalogue();
  render();
})();
