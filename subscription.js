let subscriptionAccess=null,subscriptionCsrf='',subscriptionLocked=false;
const accessText=s=>s.status==='owner'?'Кабинет владельца: доступ без ограничений подписки.':s.status==='pending'?'Демо доступно. Семь бесплатных дней начнутся после первой успешной загрузки настоящего магазина.':s.status==='expired'?'Пробный или оплаченный доступ закончился. Данные сохранены. Для продолжения нужна оплата.':s.status==='trial'?'Пробный доступ: осталось '+s.days_left+' дн.':'Подписка действует: осталось '+s.days_left+' дн.';
window.renderSubscription=function(){
 const s=current?.subscription||subscriptionAccess;if(!s)return;
 $('subscription-status').textContent=accessText(s);
 const web=location.pathname==='/web-app'&&!window.MINI_APP_MODE;
 document.querySelector('.plan-card').hidden=Boolean(window.MINI_APP_MODE);
 const button=$('buy-subscription');button.disabled=!(web&&s.checkout_enabled&&$('accept-purchase-terms').checked);$('purchase-terms').hidden=!(web&&s.checkout_enabled);if(s.terms_url?.startsWith('https://'))$('purchase-terms-link').href=s.terms_url;
 button.textContent=web&&s.checkout_enabled?'Оплатить '+s.price_rub+' ₽ по СБП':'Оплата пока не подключена';
 document.querySelector('.plan-card>strong').textContent=s.checkout_enabled?s.price_rub+' ₽ / 30 дней':'Цена согласовывается';
 $('link-telegram').hidden=!web;$('web-logout').hidden=!web;
 if(current?.account?.web){document.querySelector('[data-provider="telegram"]').hidden=true;document.querySelector('a[href="#bot-builder"] .tool-icon')?.closest('a')?.setAttribute('hidden','');}
 if(current?.account?.demo){
  $('subscription-status').textContent='У вас отдельный бесплатный демо-магазин. Товары вымышленные; оплата и подключение настоящих ключей отключены.';
  document.querySelector('.plan-card').hidden=true;
  document.querySelectorAll('.connection-form').forEach(form=>{form.hidden=true});
 }
};
window.showSubscriptionLock=function(data){
 subscriptionAccess=data.subscription;subscriptionCsrf=data.csrf||subscriptionCsrf;subscriptionLocked=true;
 $('marketplace-welcome').hidden=true;document.querySelector('main').inert=false;document.querySelector('aside').inert=false;
 if(current){current.products=[];current.alerts=[];current.subscription=data.subscription}
 $('rows').replaceChildren();$('alert-list').replaceChildren();$('action-list').replaceChildren();
 location.hash='subscription';document.body.dataset.view='subscription';window.renderSubscription();
};
async function subscriptionPost(path,body={}){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':current?.csrf||subscriptionCsrf,...(window.telegramAuthHeaders?.()||{})},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw Error(data.error||'Действие не выполнено.');return data;
}
$('buy-subscription').onclick=async()=>{const b=$('buy-subscription');b.disabled=true;$('subscription-result').textContent='Создаём платёж. Не закрывайте страницу…';try{const data=await subscriptionPost('/api/billing/create-payment',{accept_terms:$('accept-purchase-terms').checked});const url=new URL(data.url);if(url.protocol!=='https:')throw Error('Платёжная ссылка не подтверждена.');location.href=url.href}catch(error){$('subscription-result').textContent=error.message}finally{window.renderSubscription()}};
$('link-telegram').onclick=async()=>{try{const data=await subscriptionPost('/api/telegram-link');const url=new URL(data.url);if(url.hostname!=='t.me'||url.protocol!=='https:')throw Error('Ссылка не подтверждена.');const a=$('telegram-link-result');a.href=url.href;a.hidden=false;$('subscription-result').textContent='Ссылка действует 10 минут. Откройте бота и нажмите Start. Не передавайте ссылку другим.'}catch(error){$('subscription-result').textContent=error.message}};
$('web-logout').onclick=async()=>{await fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});location.href='/'};
window.addEventListener('hashchange',()=>{if(subscriptionLocked&&!['#subscription','#help'].includes(location.hash))location.hash='subscription'});
if(location.pathname==='/web-app')fetch('/api/access').then(async response=>{if(response.status===401){location.href='/';return}const data=await response.json();subscriptionAccess=data.subscription;subscriptionCsrf=data.csrf;if(!data.subscription.allowed)window.showSubscriptionLock(data);else window.renderSubscription()}).catch(()=>{$('subscription-result').textContent='Не удалось проверить доступ. Обновите страницу.'});
if(current)window.renderSubscription();

$('check-payment').onclick=async()=>{try{const data=await subscriptionPost('/api/billing/check-payment');subscriptionAccess=data.subscription;if(current)current.subscription=data.subscription;if(data.subscription.allowed&&subscriptionLocked){location.reload();return}window.renderSubscription();$('subscription-result').textContent=data.subscription.status==='paid'?'Оплата подтверждена, доступ открыт.':'Подтверждённого платежа пока нет. Если деньги списаны, не оплачивайте повторно: свяжитесь с поддержкой.'}catch(error){$('subscription-result').textContent=error.message}};

$('accept-purchase-terms').onchange=()=>window.renderSubscription();
