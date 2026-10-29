let current, filter='all', loaded=false;
const $=id=>document.getElementById(id);
let marketplaceChoosing=true;
function applyMarketplace(value){
 const selected=['Ozon','WB'].includes(value), wb=value==='WB';
 document.body.dataset.marketplace=selected?value:'pending';
 $('marketplace-welcome').hidden=selected&&!marketplaceChoosing;
 document.querySelector('main').inert=!selected||marketplaceChoosing;
 document.querySelector('aside').inert=!selected||marketplaceChoosing;
 document.querySelectorAll('[data-marketplace-choice]').forEach(b=>{b.disabled=false;b.setAttribute('aria-pressed',String(b.dataset.marketplaceChoice===value))});
 $('marketplace-error').textContent='';
 $('cancel-marketplace').hidden=!selected;
 $('marketplace-switch').textContent=selected?(wb?'Wildberries':'Ozon')+' ⌄':'Выбрать площадку';
 document.querySelectorAll('[data-wb-only]').forEach(e=>e.hidden=!wb);
 document.querySelectorAll('.connection-form[data-provider]').forEach(form=>{const provider=form.dataset.provider;form.hidden=selected&&provider!=='telegram'&&provider!==(wb?'wb':'ozon')});
 $('planner-intro').textContent=wb?'Учтите дорогу, приёмку на складе WB и изменение коэффициентов.':'Учтите подготовку, доставку и обработку поставки на складе Ozon.';
 const selectedLabel=wb?'Wildberries':'Ozon';
 const eyebrow=document.querySelector('header .eyebrow');
 eyebrow.textContent=selected?selectedLabel.toUpperCase()+' · ВАШ МАГАЗИН ПОД КОНТРОЛЕМ':'ВАШ МАГАЗИН · ПОД КОНТРОЛЕМ';
}
function showMarketplace(){
 marketplaceChoosing=true;
 $('marketplace-welcome').hidden=false;
 document.querySelector('main').inert=true;document.querySelector('aside').inert=true;
 $('cancel-marketplace').hidden=!current?.marketplace;
 document.querySelector('[data-marketplace-choice]')?.focus();
}
$('marketplace-switch').onclick=showMarketplace;
$('cancel-marketplace').onclick=()=>{marketplaceChoosing=false;applyMarketplace(current?.marketplace)};
document.querySelectorAll('[data-marketplace-choice]').forEach(button=>button.onclick=async()=>{
 if(!current)return;
 document.querySelectorAll('[data-marketplace-choice]').forEach(b=>b.disabled=true);
 $('marketplace-error').textContent='Открываем магазин…';
 try{
  await post('/api/marketplace',{marketplace:button.dataset.marketplaceChoice});
  marketplaceChoosing=false;
  selectedProduct='';formProduct='';filter='all';$('search').value='';
  document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter==='all'));
  location.hash='overview';lastPayload='';await load();
 }catch(error){$('marketplace-error').textContent=error.message}
 finally{document.querySelectorAll('[data-marketplace-choice]').forEach(b=>b.disabled=false)}
});
const levels={empty:'Нет в наличии',low:'Мало товара',soon:'Пора пополнить',supply_due:'Пора оформлять поставку',ok:'В порядке'};
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function date(value){return value?new Date(value).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'ещё не обновлялось'}
function rows(){const body=$('rows');body.replaceChildren();const query=$('search').value.toLowerCase();const list=current.products.filter(p=>(filter==='all'||p.level!=='ok')&&(p.name+' '+p.offer).toLowerCase().includes(query));for(const p of list){const tr=node('tr'),name=node('td');name.append(node('strong',p.name),node('small',p.offer));tr.append(name);const platform=node('td');platform.append(node('span',p.platform,'badge '+(p.platform==='WB'?'wb':'ozon')));tr.append(platform);const price=node('td',p.price!=null?exactMoney(p.price):p.price_min!=null?exactMoney(p.price_min)+' – '+exactMoney(p.price_max):'Нет данных');const dimensions=node('td',p.dimensions?`${p.dimensions.length_cm} × ${p.dimensions.width_cm} × ${p.dimensions.height_cm} см`:'Нет данных');const profit=node('td');const profitButton=node('button',p.economics?.profit!=null?exactMoney(p.economics.profit):'Указать расходы','profit-button'+(p.economics?.profit<0?' loss':''));profitButton.type='button';profitButton.onclick=()=>window.openEconomics?.(p.id);profit.append(profitButton);tr.append(price,dimensions,profit,node('td',p.quantity+' шт.'),node('td',p.daily===null?'Нет данных':p.daily.toFixed(1)),node('td',p.coverage===null?'—':p.coverage.toFixed(1)+' дн.'));const status=node('td');status.append(node('span',levels[p.level],'badge '+p.level));tr.append(status);body.append(tr)}if(!list.length){const td=node('td','Товары не найдены.');td.colSpan=9;const emptyRow=node('tr');emptyRow.append(td);body.append(emptyRow)}}
function render(){const s=current;applyMarketplace(s.marketplace);const demo=s.meta.mode!=='live';$('mode').textContent=demo?'ДЕМО · '+(s.marketplace==='WB'?'Wildberries':s.marketplace||'Ozon и WB')+' — вымышленные данные. Подключите API магазина для реальных остатков.':'Данные из настроенных API. Интеграции без проверки на живом магазине. Тарифы и источники показаны отдельно.';for(const key of ['error','warning']){$(key).hidden=!s.meta[key];$(key).textContent=s.meta[key]||''}$('count').textContent=s.products.length;$('risk').textContent=s.products.filter(p=>p.level!=='ok').length;$('empty').textContent=s.products.filter(p=>p.quantity===0).length;$('updated').textContent='Обновлено: '+date(s.meta.updated);$('refresh').disabled=s.busy;$('refresh').textContent=s.busy?'Обновление…':'↻ Обновить данные';$('connection').textContent=(s.marketplace==='WB'?'Wildberries':s.marketplace||'Все площадки')+(demo?' · демо':' · магазин');if(!loaded){$('threshold').value=s.settings.threshold;$('days').value=s.settings.days;loaded=true}$('telegram-state').textContent=demo?'Автоуведомления демо отключены. Подключение и команды бота — в разделе «Студия».':s.telegram?'Настроен на сервере. Статус каждой отправки — в ленте.':'Не настроен. Уведомления сохраняются в очереди; отправки пока нет.';const feed=$('alert-list');feed.replaceChildren();for(const a of s.alerts.slice(0,5)){const div=node('div',undefined,'event');div.append(node('span','◉','event-icon'));const text=node('div');const status={demo:'Демо · не отправлено',pending:a.attempts>=5?'Доставка приостановлена после 5 попыток':'Ожидает отправки',sent:'Отправлено в Telegram',muted:'Отключено в конструкторе',obsolete:'Не отправлено: ситуация изменилась'};text.append(node('p',a.message),node('small',date(a.created)+' · '+status[a.status]));div.append(text);feed.append(div)}if(!s.alerts.length)feed.append(node('p','Пока нет уведомлений.','footnote'));rows();renderPlanner();renderActions();if(window.renderStudio)window.renderStudio();window.renderEconomics?.();window.renderSubscription?.()}
let loading=false,lastPayload='',pollTimer;
async function responseJson(response){try{return await response.json()}catch(_){throw Error('Внешнее соединение прервалось. Закройте приложение, отправьте боту /start и откройте его новой кнопкой.')}}
async function load(){
 if(loading||document.hidden)return;
 loading=true;const controller=typeof AbortController!=='undefined'?new AbortController():null;
 const timeout=setTimeout(()=>controller?.abort(),12000);
 try{
  const r=await fetch('/api/state',{headers:window.telegramAuthHeaders?.()||{},...(controller?{signal:controller.signal}:{})});
  if(!r.ok){const error=await responseJson(r);if(r.status===402){window.showSubscriptionLock?.(error);window.APP_DATA_READY=true;return}if(r.status===401&&location.pathname==='/web-app'){location.href='/';return}throw Error(error.error||'Сервер недоступен')}
  const data=await responseJson(r),payload=JSON.stringify(data);current=data;
  if(payload!==lastPayload){render();lastPayload=payload}
  else{$('error').hidden=!data.meta.error;$('error').textContent=data.meta.error||''}
  window.APP_DATA_READY=true;$('startup-retry').hidden=true;
 }catch(e){
  const message=e.name==='AbortError'?'Сервер не ответил за 12 секунд. Повторите подключение.':e.message||'Не удалось связаться с сервером.';
  $('error').hidden=false;$('error').textContent=message;
  if(!window.APP_DATA_READY)window.startupFailure?.(message);
 }finally{clearTimeout(timeout);loading=false;clearTimeout(pollTimer);pollTimer=setTimeout(load,current?.busy?2500:30000)}
}
window.retryAppLoad=()=>{window.STARTUP_FAILED=false;$('marketplace-error').textContent='Проверяем соединение…';lastPayload='';load()};
document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);if(!document.hidden)load()});

async function post(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':current.csrf,...(window.telegramAuthHeaders?.()||{})},body:JSON.stringify(data||{})});const answer=await responseJson(r);if(!r.ok)throw Error(answer.error||'Не удалось выполнить действие');return answer}
$('refresh').onclick=async()=>{try{$('refresh').disabled=true;await post('/api/sync');await load()}catch(e){$('error').hidden=false;$('error').textContent=e.message}};
$('search').oninput=()=>current&&rows();document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));rows()});
$('rules').onsubmit=async e=>{e.preventDefault();try{await post('/api/settings',{threshold:Number($('threshold').value),days:Number($('days').value)});$('saved').textContent='Сохранено. Новые правила уведомлений применятся при следующей проверке.';await load()}catch(e){$('saved').textContent=e.message}};
load();

let selectedProduct='', formProduct='';
const profileDefaults={preparation:2,transit:3,acceptance_min:2,acceptance_max:10,safety:2,wait:3,batch:100,margin:200,base_fee:1000,coefficient_now:null,coefficient_wait:null,seasonal_factor:1,warehouse:'',warehouse_id:0,box_type:2};
const exactMoney=value=>value.toLocaleString('ru-RU',{maximumFractionDigits:2})+' ₽';
const money=value=>value.toLocaleString('ru-RU',{maximumFractionDigits:0})+' ₽';
const calendarDay=value=>new Date(value+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'long'});
const range=(a,b,unit='')=>Math.abs(a-b)<.05?a.toFixed(1)+unit:a.toFixed(1)+'–'+b.toFixed(1)+unit;
function renderPlanner(){
 const picker=$('plan-product'), products=current.products;
 if(!products.some(p=>p.id===selectedProduct))selectedProduct=(products.find(p=>p.platform==='WB')||products[0]||{}).id||'';
 const signature=products.map(p=>p.id+'|'+p.name).join(';');
 if(picker.dataset.signature!==signature){picker.replaceChildren();for(const p of products){const option=node('option',p.platform+' · '+p.name);option.value=p.id;picker.append(option)}picker.dataset.signature=signature}
 picker.value=selectedProduct;
 const p=products.find(p=>p.id===selectedProduct);
 if(!p){$('plan-advice').textContent='Пока нет данных товаров.';$('plan-facts').replaceChildren();$('scenario-cards').replaceChildren();$('comparison').textContent='';return}
 const plan=p.plan, model=plan.model;
 $('plan-advice').textContent=plan.advice;
 const facts=$('plan-facts');facts.replaceChildren();
 const fact=(text,warn=false)=>facts.append(node('span',text,'fact'+(warn?' warn':'')));
 fact('Средние заказы за 14 дней: '+(model.baseline===null?'нет данных':model.baseline.toFixed(1)+' шт./день'));
 if(model.recent!==null)fact('Последние 3 дня: '+model.recent.toFixed(1)+' шт./день',model.spike);
 if(model.spike)fact('Всплеск спроса: учтён в осторожном сценарии',true);
 
 
 if(plan.configured&&plan.lead_min!==undefined){fact('Новая партия появится в продаже через '+plan.lead_min+'–'+plan.lead_max+' дней');fact('Товар может отсутствовать: '+range(plan.gap_min,plan.gap_max,' дн.'),plan.gap_max>0)}
 const cards=$('scenario-cards');cards.replaceChildren();
 for(const scenario of plan.scenarios){const card=node('article',undefined,'scenario');card.append(node('h3',scenario.label));card.append(node('p','Приёмка + оценка недополученного дохода','scenario-total-label'));card.append(node('strong',scenario.total_min===null?'Нужны расходы для сравнения в ₽':money(scenario.total_min)+' – '+money(scenario.total_max)));card.append(node('p','Приёмка всей партии: '+(scenario.fee===null?'неизвестна':money(scenario.fee))));card.append(node('p','Товар появится в продаже: '+calendarDay(scenario.arrival_min)+' — '+calendarDay(scenario.arrival_max)));card.append(node('p','Возможный перерыв в продажах: '+range(scenario.gap_min,scenario.gap_max,' дн.')));card.append(node('p','Оценка заказов, которые могут не состояться за '+plan.horizon_days+' дн.: '+range(scenario.missed_min,scenario.missed_max,' шт.')));cards.append(card)}
 $('comparison').textContent=plan.comparison||'Укажите сроки до появления товара в продаже. Для денежного сравнения добавьте стоимость приёмки и доход с продажи.';
 let tariffText='Стоимость = приёмка + оценка недополученного дохода на одинаковом горизонте. Это не точный убыток и не вероятность. ';
 const tariffs=current.tariffs;
 if(current.meta.mode==='demo')tariffText+='Тарифы и история демонстрационные; коэффициенты сценариев заданы в настройках.';
 else if(plan.tariff_source==='wb_api')tariffText+='Коэффициент отправки сейчас взят из WB для выбранной даты прибытия и упаковки. Обновлено '+date(tariffs.updated)+'. Коэффициент ожидания — предположение продавца.';
 else if(current.marketplace==='Ozon')tariffText+='Для Ozon стоимость приёмки задаётся вручную. Коэффициенты WB здесь не используются.';
 else tariffText+='Автоматический тариф не подтверждён: коэффициенты сценариев — ваши допущения. '+(tariffs?.error||'Нет свежего подходящего тарифа для склада, даты и упаковки.');
 $('tariff-status').textContent=tariffText;
 if(formProduct!==p.id){const profile=p.profile||{...profileDefaults,...(p.platform==='Ozon'?{acceptance_min:1,acceptance_max:3}:{})};for(const [key,value] of Object.entries(profile)){const input=$('route-form').elements.namedItem(key);if(input)input.value=value??''}formProduct=p.id;$('route-saved').textContent=p.profile?'':'Заполните маршрут: предложенные числа — пример, пока не применяются.'}
}
$('plan-product').onchange=()=>{selectedProduct=$('plan-product').value;renderPlanner()};
$('route-form').onsubmit=async e=>{e.preventDefault();const profile={};for(const key of Object.keys(profileDefaults)){const value=$('route-form').elements.namedItem(key).value;profile[key]=key==='warehouse'?value:(key.startsWith('coefficient_')&&value==='')?null:Number(value)}try{await post('/api/profile',{product:selectedProduct,profile});$('route-saved').textContent='Сохранено. Расчёт обновлён. Будущий тариф остаётся вашим допущением.';await load()}catch(error){$('route-saved').textContent=error.message}};

const themeButton=$('theme-toggle');
function applyTheme(theme){document.body.dataset.theme=theme;themeButton.textContent=theme==='dark'?'☀ Светлая тема':'☾ Тёмная тема';localStorage.setItem('zapas-theme',theme)}
applyTheme(localStorage.getItem('zapas-theme')||'dark');
themeButton.onclick=()=>applyTheme(document.body.dataset.theme==='dark'?'light':'dark');
function navigate(){const view=location.hash.slice(1)||'overview';const valid=['overview','products','alerts','settings','studio','connections','site-builder','bot-builder','help','economics','subscription'];const active=valid.includes(view)?view:'overview';document.body.dataset.view=active;document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.hash==='#'+active||(a.hash==='#studio'&&['connections','site-builder','bot-builder'].includes(active))));const texts={economics:['Прибыль по товарам','Проверьте, сколько остаётся с продажи и какая цена покрывает расходы.'],subscription:['Подписка','Пробный доступ, срок действия и условия тарифа.'],help:['Помощь','Пошаговая инструкция, команды и ответы на частые вопросы.'],studio:['Студия','Соберите страницу продукта и помощника в Telegram.'],connections:['Подключения','Ключи и доступы — в одном месте.'],"site-builder":['Конструктор сайта','Тексты и оформление вашей страницы.'],"bot-builder":['Конструктор бота','Меню, ответы и уведомления помощника.'],overview:['Обзор магазина','Запасы, ближайшие действия и цена ожидания — в одном месте.'],products:['Товары и остатки','Найдите нужный артикул и проверьте, какие товары пора пополнять.'],alerts:['Уведомления','События магазина и понятный статус каждой отправки.'],settings:['Настройки контроля','Выберите, когда предупреждать о заканчивающихся товарах.']};document.querySelector('header h1').textContent=texts[active][0];document.querySelector('header p').textContent=texts[active][1];window.scrollTo(0,0)}
window.addEventListener('hashchange',navigate);navigate();

function renderActions(){const list=$('action-list');list.replaceChildren();const items=current.products.filter(p=>p.level!=='ok');$('action-summary').textContent=items.length?'Товаров для проверки: '+items.length:'Срочных действий нет';for(const p of items){const card=node('button',undefined,'action-item');card.type='button';const text=node('span');text.append(node('strong',p.name),node('small',p.plan?.advice||levels[p.level]));card.append(text,node('span','Разобрать →'));card.onclick=()=>{selectedProduct=p.id;renderPlanner();$('planner').scrollIntoView({block:'start',behavior:'auto'})};list.append(card)}if(!items.length)list.append(node('p','Запасов достаточно по текущим правилам.','footnote'))}
