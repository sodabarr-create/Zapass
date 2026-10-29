"""Explicitly update the bot menu after the permanent HTTPS site is live."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from bot_content import COMMANDS
from deployment import validate_environment,configure_public_app
validate_environment()
app.init()
app.restore_credentials()
configure_public_app(app.DB)
token=app.credential('TELEGRAM_BOT_TOKEN')
if not token:raise SystemExit('Не задан токен бота.')
def call(method,payload):
    result=app.request_json('https://api.telegram.org/bot'+token+'/'+method,payload)
    if not result.get('ok'):raise SystemExit('Telegram не подтвердил '+method+'. Проверьте настройки.')
    return result.get('result')
info=call('getWebhookInfo',{})
if info.get('url'):raise SystemExit('Обнаружен webhook другого подключения. Изменения не выполнены.')
url=app.workspace_config()['bot']['app_url']
if not url.startswith('https://'):raise SystemExit('Сначала задайте постоянный адрес HTTPS.')
call('setMyCommands',{'commands':COMMANDS})
button={'type':'web_app','text':'Открыть Запас','web_app':{'url':url}}
call('setChatMenuButton',{'menu_button':button})
call('setChatMenuButton',{'chat_id':app.credential('TELEGRAM_CHAT_ID'),'menu_button':button})
print('Команды и кнопка приложения обновлены. Отправьте боту /start заново.')
