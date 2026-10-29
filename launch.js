window.APP_DATA_READY=false;window.STARTUP_FAILED=false;
window.startupFailure=function(message){
 window.STARTUP_FAILED=true;
 const text=document.getElementById('marketplace-error');
 if(text)text.textContent=message;
 const retry=document.getElementById('startup-retry');if(retry)retry.hidden=false;
};
document.getElementById('startup-retry')?.addEventListener('click',()=>{
 if(window.retryAppLoad){window.retryAppLoad();return}location.reload();
});
setTimeout(()=>{if(!window.APP_DATA_READY&&!window.STARTUP_FAILED)window.startupFailure('Загрузка затянулась. Проверьте интернет и нажмите «Повторить подключение». Если ссылка из старого сообщения — откройте приложение кнопкой меню бота.');},10000);
