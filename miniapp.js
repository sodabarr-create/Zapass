// initData is kept in memory and sent only to this origin, never in URL/storage.
window.MINI_APP_MODE=true;
// Telegram puts signed initData in its launch fragment. This fallback still passes
// through full server-side signature, age, and owner verification.
const launchInitData=new URLSearchParams(location.hash.replace(/^#/,'' )).get('tgWebAppData')||'';
window.telegramAuthHeaders=()=>({'X-Telegram-Init-Data':window.Telegram?.WebApp?.initData||launchInitData});
document.body.classList.add('telegram-miniapp');
const telegramApp=window.Telegram?.WebApp;
if(telegramApp){telegramApp.ready();telegramApp.expand();telegramApp.setHeaderColor('#10172a');telegramApp.setBackgroundColor('#0b1020');telegramApp.BackButton.onClick(()=>{if(location.hash&&location.hash!=='#overview')location.hash='overview';else telegramApp.close()});const updateBack=()=>{if(location.hash&&location.hash!=='#overview')telegramApp.BackButton.show();else telegramApp.BackButton.hide()};window.addEventListener('hashchange',updateBack);updateBack()}
