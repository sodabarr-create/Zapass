"""Telegram Mini App signature validation and launch-button configuration."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlsplit


def validate_app_url(value):
    if not isinstance(value,str) or len(value)>500:
        raise ValueError('Проверьте адрес приложения.')
    if not value:
        return ''
    p=urlsplit(value)
    if (p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment
            or p.hostname in ('localhost','127.0.0.1','::1') or p.path.rstrip('/')!='/miniapp'):
        raise ValueError('Нужен публичный HTTPS-адрес вида https://ваш-домен/miniapp без параметров.')
    return value.rstrip('/')


def verify_init_data(raw, token, owner, timestamp=None):
    if not token or not owner or not raw or len(raw)>16384:
        raise ValueError('Откройте приложение кнопкой своего Telegram-бота.')
    pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
    if len(pairs)!=len({key for key,_ in pairs}):
        raise ValueError('Повторяющиеся поля Telegram.')
    values=dict(pairs)
    supplied=values.pop('hash','')
    # Telegram's bot-token HMAC algorithm includes all received fields except hash.
    check='\n'.join(f'{key}={value}' for key,value in sorted(values.items()))
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied,expected):
        raise ValueError('Не удалось проверить подпись Telegram. Откройте приложение заново.')
    try:
        age=(time.time() if timestamp is None else timestamp)-int(values['auth_date'])
        user=json.loads(values['user'])
        user_id=user['id']
    except (ValueError,KeyError,TypeError):
        raise ValueError('Неполные данные входа Telegram.') from None
    if not 0<=age<=3600:
        raise ValueError('Вход устарел. Закройте приложение и откройте из бота заново.')
    if type(user_id) is not int or str(user_id)!=str(owner):
        raise ValueError('Это приложение доступно только владельцу подключённого магазина.')
    return user_id


def bot_keyboard(config):
    keys=[[{'text':config['summary_label']}],[{'text':config['stock_label']},{'text':'Сроки поставок'}],
          [{'text':'Последние события'},{'text':config['help_label']}],[{'text':'Мой доступ'}]]
    url=config.get('app_url','')
    if url:
        keys.insert(0,[{'text':'Открыть приложение','web_app':{'url':validate_app_url(url)}}])
    return dict(keyboard=keys,resize_keyboard=True)

def verify_registered_user(raw,token):
    """Validate the same Telegram signature, returning identity without owner policy."""
    try:
        values=dict(parse_qsl(raw,keep_blank_values=True,strict_parsing=True))
        user=json.loads(values['user'])['id']
        if type(user) is not int or user<=0:raise ValueError()
    except (ValueError,KeyError,TypeError):raise ValueError('Не удалось определить пользователя Telegram.') from None
    return verify_init_data(raw,token,str(user))
