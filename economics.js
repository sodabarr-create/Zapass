let ecoProduct='',ecoHydrated='',ecoDirty=false;
const ecoLabels={price:'Цена продажи',cost:'Себестоимость',commission_pct:'Комиссия площадки',tax_pct:'Налог',acquiring_pct:'Эквайринг',logistics:'Логистика',storage:'Хранение',returns:'Резерв на возвраты',other:'Другие расходы'};
window.openEconomics=id=>{ecoProduct=id;ecoHydrated='';location.hash='economics';window.renderEconomics()};
window.renderEconomics=function(){
 if(!current||!$('economics-product'))return;
 const products=current.products,picker=$('economics-product');
 if(!products.some(p=>p.id===ecoProduct))ecoProduct=products[0]?.id||'';
 const sig=products.map(p=>p.id+'|'+p.name).join(';');
 if(picker.dataset.signature!==sig){picker.replaceChildren();for(const p of products){const option=node('option',p.offer+' · '+p.name);option.value=p.id;picker.append(option)}picker.dataset.signature=sig}
 picker.value=ecoProduct;const product=products.find(p=>p.id===ecoProduct);
 if(!product){$('economics-result').textContent='Пока нет товаров выбранной площадки.';return}
 const e=product.economics;
 if(ecoHydrated!==product.id){ecoDirty=false;for(const key of Object.keys(ecoLabels))$('economics-form').elements.namedItem(key).value=e.inputs[key]??'';const priceInput=$('economics-form').elements.namedItem('price');priceInput.dataset.auto=e.inputs.price==null&&e.price!=null?'1':'0';if(priceInput.dataset.auto==='1')priceInput.value=e.price;ecoHydrated=product.id;$('economics-saved').textContent=''}
 if(ecoDirty)return;
 const automatic=$('economics-form').elements.namedItem('price').dataset.auto==='1';
 $('economics-price-mode').textContent=automatic?'Цена подставлена из магазина. Можно ввести другую.':'Ваша цена для расчёта. Очистите поле, чтобы снова взять цену магазина.';
 $('economics-source').textContent=e.price_source==='api'?(current.meta.mode==='demo'?'Демо: цена и расходы вымышленные.':'Цена получена из магазина. Используется цена продавца, без дополнительных скидок за счёт площадки.'):'Расчёт по указанной вами цене и расходам.';
 const result=$('economics-result');result.replaceChildren();result.classList.remove('is-stale');
 if(e.missing.length){
  result.append(node('span','РАСЧЁТ ПО ОДНОЙ ПРОДАЖЕ','eyebrow'),node('h3','Осталось заполнить '+e.missing.length+' '+(e.missing.length===1?'поле':e.missing.length<5?'поля':'полей')),node('p','Без этих данных результат будет неточным:','result-note'));
  const list=node('ul',undefined,'missing-costs');
  for(const key of e.missing){const item=node('li'),button=node('button',ecoLabels[key],'missing-field');button.type='button';button.onclick=()=>{if(['tax_pct','acquiring_pct','storage','returns','other'].includes(key))$('economics-extra').open=true;const input=$('economics-form').elements.namedItem(key);input.focus();input.scrollIntoView({block:'center',behavior:'auto'})};item.append(button);list.append(item)}result.append(list,node('p','Нажмите на поле в списке, чтобы перейти к нему. Если расхода нет, укажите 0.','result-note'));return;
 }
 const summary=node('article',undefined,'profit-summary'+(e.profit<0?' negative':''));
 summary.append(node('small',e.profit<0?'С каждой продажи вы теряете':'С одной продажи у вас останется'),node('strong',exactMoney(Math.abs(e.profit))),node('p','После всех указанных расходов. Это оценка, не выплата от площадки.'));
 const equation=node('div',undefined,'profit-equation');equation.append(node('span','Цена '+exactMoney(e.price)),node('span','− расходы '+exactMoney(e.price-e.profit)),node('span','= '+(e.profit<0?'убыток ':'доход ')+exactMoney(Math.abs(e.profit))));
 result.append(node('span','РАСЧЁТ ПО ОДНОЙ ПРОДАЖЕ','eyebrow'),summary,equation);
 const floor=node('div',undefined,'price-floor');floor.append(node('strong','Чтобы продавать без убытка'),node('p',e.breakeven==null?'При этих процентах расходы не покрываются повышением цены. Проверьте ставки.':'Цена должна быть не ниже '+exactMoney(e.breakeven)+' при тех же расходах.'));result.append(floor);
 const details=node('details',undefined,'cost-details');details.append(node('summary','Посмотреть все расходы'));
 const breakdown=node('dl',undefined,'cost-breakdown');for(const row of e.breakdown)breakdown.append(node('dt',ecoLabels[row.key]),node('dd',exactMoney(row.amount)));details.append(breakdown,node('p','Остаётся '+(e.margin_pct==null?'—':e.margin_pct.toLocaleString('ru-RU'))+'% от цены продажи.','result-note'));result.append(details);
};
$('economics-product').onchange=()=>{ecoProduct=$('economics-product').value;ecoHydrated='';window.renderEconomics()};
$('economics-form').onsubmit=async event=>{event.preventDefault();const costs={};for(const key of Object.keys(ecoLabels)){const value=$('economics-form').elements.namedItem(key).value;costs[key]=(key==='price'&&$('economics-form').elements.namedItem('price').dataset.auto==='1')||value===''?null:Number(value)}const button=event.submitter;button.disabled=true;try{await post('/api/economics',{product:ecoProduct,costs});ecoHydrated='';ecoDirty=false;lastPayload='';await load();$('economics-saved').textContent='Готово. Результат обновлён.';if(window.innerWidth<781)$('economics-result').scrollIntoView({block:'start',behavior:'auto'})}catch(error){$('economics-saved').textContent=error.message}finally{button.disabled=false}};
$('economics-form').addEventListener('input',event=>{ecoDirty=true;if(event.target.name==='price'){event.target.dataset.auto='0';$('economics-price-mode').textContent='Вы изменили цену для этого расчёта.'}$('economics-saved').textContent='Есть изменения. Нажмите «Рассчитать прибыль».';$('economics-result').classList.add('is-stale')});
if(current)window.renderEconomics();
