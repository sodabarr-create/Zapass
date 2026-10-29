"""Production configuration; contains no credentials."""
import os
import json
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit


def validate_environment():
    if os.getenv('ZAPAS_ENV') != 'production':
        return
    from cryptography.fernet import Fernet
    try:
        Fernet(os.environ['ZAPAS_MASTER_KEY'].encode('ascii'))
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ValueError('Задайте корректный ZAPAS_MASTER_KEY в .env.') from exc
    origin=urlsplit(os.getenv('ZAPAS_WEB_ORIGIN',''))
    if (origin.scheme!='https' or not origin.hostname or origin.username or origin.password
            or origin.path not in ('','/') or origin.query or origin.fragment):
        raise ValueError('ZAPAS_WEB_ORIGIN должен быть адресом HTTPS без пути.')
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID','').isdigit():
        raise ValueError('Задайте токен бота и числовой TELEGRAM_CHAT_ID владельца.')


def configure_public_app(database):
    origin=os.getenv('ZAPAS_WEB_ORIGIN','').rstrip('/')
    if not origin:
        return
    from studio import BOT
    with closing(sqlite3.connect(database)) as con, con:
        row=con.execute("SELECT payload FROM workspace WHERE key='bot'").fetchone()
        config={**BOT,**(json.loads(row[0]) if row else {})}
        config['app_url']=origin+'/miniapp'
        if 'ZAPAS_BOT_ENABLED' in os.environ:
            config['enabled']=os.environ['ZAPAS_BOT_ENABLED']=='1'
        con.execute("INSERT OR REPLACE INTO workspace VALUES('bot',?)",(json.dumps(config),))
