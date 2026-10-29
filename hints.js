// One shared tooltip, no polling or continuous animation. Hover, focus, tap, Escape.
(()=>{
const text={
price:'Цена одного товара после вашей скидки. Обычно подставляется из API магазина. Можно изменить для проверки другой цены. Не добавляйте скидку за счёт площадки, если она не уменьшает вашу выручку.',
cost:'Сколько вам обходится одна единица товара: закупка, производство и подготовка. Например, партию из 100 штук купили за 40 000 ₽ — укажите 400 ₽. Доставку покупателю вводите отдельно.',
commission_pct:'Процент комиссии вашей площадки для категории и схемы продаж. Возьмите его из кабинета продавца. Например, при цене 1000 ₽ комиссия 25% равна 250 ₽.',
tax_pct:'Процент от цены продажи, который вы хотите заложить в расчёт налога. Используйте свою ставку. Если налог считается с другой базы, здесь нужна ваша эффективная ставка. Мы не определяем налоговый режим автоматически.',
acquiring_pct:'Отдельная плата за приём оплаты, в процентах от цены. Не учитывайте её второй раз, если она уже включена в другую статью расходов. Если отдельной платы нет — 0.',
logistics:'Сколько стоит доставка одной проданной единицы по вашей схеме работы. Укажите среднюю сумму в рублях из отчёта площадки. Не стоимость доставки всей поставки.',
storage:'Средняя стоимость хранения, приходящаяся на одну продажу. Например, 1000 ₽ хранения на 100 проданных товаров — 10 ₽. Если хранения нет — 0.',
returns:'Средние расходы на возвраты в пересчёте на одну продажу. Например, 1000 ₽ расходов на 100 продаж — 10 ₽. Это ваша оценка, а не автоматический прогноз возвратов.',
other:'Остальные расходы на один проданный товар: упаковка, реклама и другое. Не включайте то, что уже внесено в соседние поля. Если расходов нет — 0.',
preparation:'Сколько дней нужно, чтобы подготовить новую партию к отправке: закупка, производство, упаковка. Считайте с момента решения о пополнении.',
transit:'Сколько дней партия едет до склада площадки. Приёмку и размещение на складе укажите отдельно.',
acceptance_min:'Самое короткое реалистичное время от доставки на склад до появления товара в продаже. Например, обычно 2–10 дней — здесь укажите 2.',
acceptance_max:'Осторожная оценка времени до появления товара в продаже после доставки. При диапазоне 2–10 дней укажите 10. Это не срок, обещанный площадкой.',
safety:'На сколько дополнительных дней продаж вы хотите сохранить запас на случай задержки. Например, 2 дня. Это запас времени, а не ещё один этап доставки.',
wait:'Сколько дней вы предлагаете подождать перед началом подготовки новой партии во втором варианте. При 3 днях вся цепочка поставки начнётся на 3 дня позже.',
batch:'Сколько единиц будет в новой партии. Не текущий остаток. Число используется при сравнении двух поставок.',
seasonal_factor:'Ожидаемое изменение спроса по вашей оценке: 1 — без изменений, 1,5 — рост на 50%, 0,8 — снижение на 20%. Это поправка на акцию или сезон, а не прогноз из календаря.',
margin:'Сколько остаётся с одной продажи после ваших расходов, в рублях. Это не цена товара. Посмотрите оценку в разделе «Прибыль». Значение используется для оценки дохода, который можно недополучить при отсутствии товара.',
base_fee:'Стоимость приёмки всей новой партии при коэффициенте 1. Не цена за единицу. Например, базовая приёмка партии 1000 ₽ при коэффициенте 2 обойдётся в 2000 ₽.',
coefficient_now:'Во сколько раз увеличивается базовая приёмка для отправки сейчас: 0 — бесплатно, 1 — базовая цена, 2 — вдвое дороже. Если не знаете, оставьте пустым: денежный итог не будет выдуман.',
coefficient_wait:'Какой коэффициент приёмки вы предполагаете после ожидания. Это будущая цена, её нельзя гарантировать. Если не знаете — оставьте поле пустым.',
warehouse:'Название склада для ваших заметок. Для поиска подходящего тарифа WB дополнительно нужен числовой ID склада.',
warehouse_id:'Числовой ID склада WB. Название склада его не заменяет. Нужен, чтобы сопоставить тариф с выбранным складом; без подходящего API-ответа тариф остаётся ручным.',
box_type:'ID типа упаковки WB для сопоставления тарифа. Это код из справочника площадки, а не количество коробок. Если код неизвестен, не считайте автоматический тариф подтверждённым.',
threshold:'Предупредить, когда доступный остаток не больше этого количества. Например, 10 — сигнал при остатке 10 штук и меньше.',
days:'Предупредить, когда остатка хватит не больше чем на указанное число дней при текущем темпе заказов. Например, 7 — сигнал примерно за неделю до окончания товара.',
OZON_CLIENT_ID:'Client ID вашего магазина Ozon. Берётся в кабинете продавца вместе с API-ключом. Не Telegram ID.',
OZON_API_KEY:'API-ключ собственного магазина Ozon с доступом к нужным данным. Вводите его только здесь. Ключ не должен отправляться в переписку.',
WB_API_TOKEN:'API-токен своего магазина WB. Для цен и габаритов нужны подходящие права на цены и контент; для остатков и заказов — доступ к используемым отчётам. Сохранённый токен ещё не означает успешную загрузку.',
scenario:'Сравниваются две одинаковые партии: начать подготовку сегодня или позже. К ожиданию прибавляются подготовка, дорога и приёмка. В рублях сравниваются приёмка плюс оценка недополученного дохода за одинаковое время. Бронь и заказ автоматически не создаются.'
};
document.querySelectorAll('input,select,textarea').forEach(input=>{
 const key=input.name||input.id,help=text[key];if(!help||input.type==='checkbox')return;
 const label=input.closest('label');if(!label||label.querySelector('.help-dot'))return;
 const title=Array.from(label.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ');
 if(!title)return;input.setAttribute('aria-label',title);
 const caption=document.createElement('span');caption.className='field-caption';
 for(const child of Array.from(label.childNodes))if(child.nodeType===3)caption.append(child);
 const button=document.createElement('button');button.type='button';button.className='help-dot';button.dataset.help=key;button.textContent='?';button.setAttribute('aria-label','Подсказка: '+title);button.setAttribute('aria-expanded','false');caption.append(button);label.prepend(caption);
});
const tooltip=document.createElement('div');tooltip.id='field-tooltip';tooltip.className='field-tooltip';tooltip.setAttribute('role','tooltip');tooltip.hidden=true;document.body.append(tooltip);
let active=null,pinned=false;
function hide(){if(active){active.setAttribute('aria-expanded','false');active.removeAttribute('aria-describedby')}active=null;pinned=false;tooltip.hidden=true}
function show(button){
 if(active!==button)hide();const message=text[button.dataset.help];if(!message)return;
 active=button;tooltip.textContent=message;tooltip.hidden=false;button.setAttribute('aria-describedby',tooltip.id);button.setAttribute('aria-expanded','true');
 const r=button.getBoundingClientRect(),width=Math.min(320,window.innerWidth-32);tooltip.style.width=width+'px';
 const height=tooltip.getBoundingClientRect().height;tooltip.style.left=Math.max(16,Math.min(window.innerWidth-width-16,r.left-14))+'px';tooltip.style.top=Math.max(12,r.bottom+height+20<window.innerHeight?r.bottom+9:r.top-height-9)+'px';
}
document.addEventListener('pointerover',event=>{const button=event.target.closest('.help-dot');if(button&&!pinned)show(button)});
document.addEventListener('pointerout',event=>{if(event.target.closest('.help-dot')&&!pinned)hide()});
document.addEventListener('focusin',event=>{const button=event.target.closest('.help-dot');if(button)show(button)});
document.addEventListener('focusout',event=>{if(event.target.closest('.help-dot')&&!pinned)hide()});
document.addEventListener('click',event=>{const button=event.target.closest('.help-dot');if(button){event.preventDefault();event.stopPropagation();if(active===button&&pinned)hide();else{show(button);pinned=true}}else hide()});
document.addEventListener('keydown',event=>{if(event.key==='Escape')hide()});
window.addEventListener('resize',hide);window.addEventListener('scroll',hide,true);window.addEventListener('hashchange',hide);
})();
