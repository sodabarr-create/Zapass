"""Запас: local, single-operator marketplace monitor. Python standard library only."""
import argparse
import copy
import hashlib
import gzip
import contextvars
from contextlib import contextmanager, closing
import datetime as dt
import json
import math
import os
from pathlib import Path
import secrets
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from planner import forecast, validate_profile, DEFAULTS
from urllib.parse import urlencode
from studio import SITE, BOT, validate_site, validate_bot, render_site, site_archive, bot_reply
from vault import crypt
from telegram_app import verify_init_data, verify_registered_user, bot_keyboard
from urllib.parse import urlsplit
from http.cookies import SimpleCookie
from economics import calculate, validate_economics
from catalog import enrich as enrich_catalog
import accounts
import billing

ROOT = Path(__file__).parent
DB = Path(os.getenv('ZAPAS_DATA_DIR',str(ROOT))) / 'zapas.sqlite3'
LOCK = threading.Lock()
CSRF = secrets.token_urlsafe(32)
LAST_ATTEMPT = 0.0
ACTIVE_USER=contextvars.ContextVar('zapas_account',default=None)
USER_LOCKS={}
USER_ATTEMPTS={}

def owner_key():
    return 'tg:'+os.getenv('TELEGRAM_CHAT_ID','')

def is_owner():
    return ACTIVE_USER.get() in (None,owner_key())

def account_key():
    return ACTIVE_USER.get() or owner_key()

def is_demo_account():
    return not is_owner() and bool(ACTIVE_USER.get() and ACTIVE_USER.get().startswith('tg:'))

def current_lock():
    return LOCK if is_owner() else USER_LOCKS.setdefault(account_key(),threading.Lock())

def credential(key):
    if is_owner():return os.getenv(key)
    if key=='TELEGRAM_BOT_TOKEN':return os.getenv(key)
    if key=='TELEGRAM_CHAT_ID' and is_demo_account():return ACTIVE_USER.get().split(':',1)[1]
    with connect() as con:
        if key=='TELEGRAM_CHAT_ID':
            row=con.execute("SELECT value FROM meta WHERE key='linked_chat'").fetchone()
            return row[0] if row else None
        row=con.execute('SELECT encrypted FROM credentials WHERE key=?',(key,)).fetchone()
    if row:
        try:return crypt(row[0],True).decode()
        except (ValueError,UnicodeError):pass
    return None

@contextmanager
def account_scope(user):
    marker=ACTIVE_USER.set(user)
    try:
        init()
        with connect() as con:empty=not con.execute('SELECT 1 FROM meta WHERE key=\'mode\'').fetchone()
        if empty and not is_owner():
            seed_demo_profiles();refresh_tariffs(demo=True);save_snapshot(demo_products(),'demo')
        yield
    finally:ACTIVE_USER.reset(marker)


@contextmanager
def connect():
    con = sqlite3.connect(accounts.database_path(DB,ACTIVE_USER.get(),owner_key()), timeout=20)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init():
    DB.parent.mkdir(parents=True,exist_ok=True)
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), threshold INTEGER, days INTEGER);
        INSERT OR IGNORE INTO settings VALUES(1,10,7);
        CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, platform TEXT, offer TEXT, name TEXT,
          quantity INTEGER, reserved INTEGER, daily REAL, updated TEXT);
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, product TEXT, message TEXT,
          created TEXT, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS states(product TEXT PRIMARY KEY, level TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS details(product TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS profiles(product TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS tariffs(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, updated TEXT, source TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS workspace(key TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS credentials(key TEXT PRIMARY KEY, encrypted BLOB);
        CREATE TABLE IF NOT EXISTS economics(product TEXT PRIMARY KEY, payload TEXT);
        ''')
        if 'category' not in {r[1] for r in con.execute('PRAGMA table_info(alerts)')}:
            con.execute("ALTER TABLE alerts ADD COLUMN category TEXT DEFAULT 'stock'")


def workspace_config():
    with connect() as con:
        data={r['key']:json.loads(r['payload']) for r in con.execute('SELECT * FROM workspace')}
    return dict(site={**copy.deepcopy(SITE),**data.get('site',{})},bot={**BOT,**data.get('bot',{})})


def restore_credentials():
    with connect() as con:
        rows=con.execute('SELECT * FROM credentials').fetchall()
    for row in rows:
        if row['key'] in ('OZON_CLIENT_ID','OZON_API_KEY','WB_API_TOKEN','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'):
            try:
                if not os.getenv(row['key']):
                    os.environ[row['key']]=crypt(row['encrypted'],True).decode()
            except (ValueError, UnicodeError):
                pass


def connection_status():
    with connect() as con:
        bot_status=con.execute("SELECT value FROM meta WHERE key='bot_status'").fetchone()
        username=con.execute("SELECT value FROM meta WHERE key='bot_username'").fetchone()
    return dict(ozon=bool(credential('OZON_CLIENT_ID') and credential('OZON_API_KEY')),
        wb=bool(credential('WB_API_TOKEN')),telegram=bool(credential('TELEGRAM_BOT_TOKEN') and credential('TELEGRAM_CHAT_ID')),
        bot_status=bot_status[0] if bot_status else 'Не запущен',bot_username=username[0] if username else '')


def configure_connection(data):
    if is_demo_account():raise ValueError('Это демонстрационный кабинет. Подключение ключей здесь отключено.')
    if not isinstance(data,dict):
        raise ValueError('Проверьте поля подключения.')
    provider=data.get('provider')
    if not is_owner() and provider=='telegram':
        raise ValueError('Подключите уведомления кнопкой «Привязать мой Telegram». Токен общего бота вводить не нужно.')
    mapping={'telegram':('TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'),
             'ozon':('OZON_CLIENT_ID','OZON_API_KEY'),'wb':('WB_API_TOKEN',)}
    if provider not in mapping or set(data)-{'provider',*mapping[provider]}:
        raise ValueError('Неизвестное подключение.')
    values={key:data.get(key,'').strip() for key in mapping[provider] if isinstance(data.get(key),str)}
    if any(not values.get(key) or len(values[key])>4096 for key in mapping[provider]):
        raise ValueError('Заполните все поля подключения.')
    username=''
    if provider=='telegram':
        if not values['TELEGRAM_CHAT_ID'].lstrip('-').isdigit():
            raise ValueError('Chat ID должен быть числом.')
        token=values['TELEGRAM_BOT_TOKEN']
        if not all(c.isalnum() or c in ':_-' for c in token):
            raise ValueError('Неверный формат токена.')
        result=request_json(f'https://api.telegram.org/bot{token}/getMe',{})
        if not result.get('ok') or not result.get('result',{}).get('is_bot'):
            raise ValueError('Telegram не подтвердил токен бота.')
        info=request_json(f'https://api.telegram.org/bot{token}/getWebhookInfo',{})
        if info.get('result',{}).get('url'):
            raise ValueError('У бота уже есть другое подключение webhook. Оно не изменено; используйте отдельного бота.')
        username=result['result'].get('username','')
    # Encrypt before modifying either DB or runtime environment.
    encrypted={key:crypt(value.encode()) for key,value in values.items()}
    with connect() as con:
        for key,value in encrypted.items():
            con.execute('INSERT OR REPLACE INTO credentials VALUES(?,?)',(key,value))
        if username:
            con.execute("INSERT OR REPLACE INTO meta VALUES('bot_username',?)",(username,))
            con.execute("INSERT OR REPLACE INTO meta VALUES('bot_connected_at',?)",(str(int(time.time())),))
    if is_owner():os.environ.update(values)
    return dict(ok=True,message='Ключи сохранены на этом компьютере. '+
        ('Токен Telegram проверен. Нажмите Start в боте; затем включите ответы в конструкторе.' if provider=='telegram' else
         'Доступ к магазину будет проверен при обновлении данных.'))


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def version_assets(content):
    def replace(match):
        path=match.group(2)
        source=ROOT/'static'/path.lstrip('/')
        if source.is_file():
            return match.group(1)+path+'?v='+hashlib.sha256(source.read_bytes()).hexdigest()[:12]+match.group(3)
        return match.group(0)
    return re.sub(r'((?:src|href)=")(/[^"?#]+)(")',replace,content)


def request_json(url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 204:
                return None
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Never expose request headers, tokens or remote response bodies.
        raise RuntimeError(f'Внешний сервис вернул HTTP {exc.code}. Проверьте доступ и лимиты.') from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError('Внешний сервис недоступен или вернул некорректный ответ.') from None
    return result


def ozon_products():
    headers = {'Client-Id': credential('OZON_CLIENT_ID'), 'Api-Key': credential('OZON_API_KEY')}
    def post(path, body):
        return request_json('https://api-seller.ozon.ru' + path, body, headers)
    rows, cursor, seen = [], '', set()
    for _ in range(1000):
        data = post('/v4/product/info/stocks', {'filter': {'visibility': 'ALL'}, 'limit': 1000, 'last_id': cursor})
        result = data.get('result', data)
        if not isinstance(result, dict) or 'items' not in result:
            raise RuntimeError('Неизвестный формат остатков Ozon. Данные не заменены.')
        items = result['items']
        for item in items:
            stocks = item.get('stocks', [])
            if isinstance(stocks, dict):
                stocks = list(stocks.values())
            for stock in stocks:
                kind = stock.get('type', 'unknown')
                rows.append({'id': f"ozon:{item['product_id']}:{kind}", 'platform': 'Ozon',
                    'offer': str(item['offer_id']), 'name': f"{item['offer_id']} · {kind.upper()}",
                    'quantity': max(0, int(stock['present']) - int(stock.get('reserved', 0))),
                    'reserved': int(stock.get('reserved', 0)), 'daily': None,
                    'sku': str(stock.get('sku', ''))})
        next_cursor = str(result.get('last_id') or '')
        if not items or not next_cursor:
            break
        if next_cursor == cursor or next_cursor in seen:
            raise RuntimeError('Ошибка пагинации Ozon. Неполные данные не сохранены.')
        seen.add(next_cursor)
        cursor = next_cursor
        time.sleep(1)
    else:
        raise RuntimeError('Слишком большой каталог для первой версии.')
    # Forecast is optional; failed analytics must not invent a zero demand rate.
    warning = ''
    try:
        today = dt.datetime.now(dt.timezone.utc).date()
        histories, offset = {}, 0
        while True:
            data = post('/v1/analytics/data', {'date_from': str(today - dt.timedelta(days=56)),
                'date_to': str(today - dt.timedelta(days=1)), 'metrics': ['ordered_units'],
                'dimension': ['sku', 'day'], 'limit': 1000, 'offset': offset})
            result = data.get('result', {})
            if 'data' not in result:
                raise RuntimeError('Неизвестный формат аналитики Ozon.')
            batch = result['data']
            for row in batch:
                sku = str(row['dimensions'][0]['id'])
                day = str(row['dimensions'][1]['id'])[:10]
                dt.date.fromisoformat(day)
                histories.setdefault(sku, {})[day] = float(row['metrics'][0])
            if len(batch) < 1000:
                break
            offset += 1000
            if offset >= 100000:
                raise RuntimeError('Слишком большой отчёт аналитики.')
            time.sleep(61)  # respect analytics endpoint's per-minute limit
        for row in rows:
            values = histories.get(row['sku'], {})
            row['history'] = [{'date': str(today - dt.timedelta(days=n)),
                'units': values.get(str(today - dt.timedelta(days=n)), 0)} for n in range(1, 57)]
            row['daily'] = sum(r['units'] for r in row['history'][:14]) / 14
    except (RuntimeError, KeyError, TypeError, ValueError, IndexError):
        warning = 'Остатки обновлены. Прогноз недоступен: нет подходящих данных аналитики или доступа.'
    return rows, warning


def wb_products():
    headers = {'Authorization': credential('WB_API_TOKEN')}
    products, offset = {}, 0
    for page in range(100):
        data = request_json('https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses',
            {'nmIds': [], 'limit': 10000, 'offset': offset}, headers)
        if data is None:
            break
        items = data['data']['items']
        for item in items:
            nm = str(item['nmId'])
            p = products.setdefault(nm, dict(id='wb:'+nm, platform='WB', offer=nm,
                name='Артикул WB '+nm, quantity=0, reserved=0, daily=None))
            p['quantity'] += int(item['quantity'])
        if len(items) < 10000:
            break
        offset += len(items)
        time.sleep(21)
    else:
        raise RuntimeError('Слишком большой каталог WB для первой версии.')
    warning = ''
    try:
        today = dt.datetime.now(dt.timezone.utc).date()
        cursor = str(today - dt.timedelta(days=56))
        seen, history = set(), {}
        for page in range(100):
            batch = request_json('https://statistics-api.wildberries.ru/api/v1/supplier/orders?' +
                urlencode({'dateFrom': cursor, 'flag': 0}), None, headers)
            if not isinstance(batch, list):
                raise RuntimeError('Неизвестный формат заказов WB.')
            for order in batch:
                key = order['srid']
                if key in seen:
                    continue
                seen.add(key)
                if order.get('isCancel'):
                    continue
                day = str(order['date'])[:10]
                if day >= str(today):
                    continue
                nm = str(order['nmId'])
                history.setdefault(nm, {})[day] = history.get(nm, {}).get(day, 0) + 1
            if len(batch) < 80000:
                break
            next_cursor = batch[-1]['lastChangeDate']
            if next_cursor <= cursor:
                raise RuntimeError('Неполная выгрузка заказов WB.')
            cursor = next_cursor
            time.sleep(61)
        else:
            raise RuntimeError('Слишком большой отчёт заказов WB.')
        for nm, p in products.items():
            p['history'] = [{'date': str(today - dt.timedelta(days=n)),
                'units': history.get(nm, {}).get(str(today - dt.timedelta(days=n)), 0)} for n in range(1, 57)]
            p['daily'] = sum(r['units'] for r in p['history'][:14]) / 14
    except (RuntimeError, KeyError, TypeError, ValueError):
        warning = 'WB: остатки получены; история заказов недоступна, прогноз отключён.'
    return list(products.values()), warning


def refresh_tariffs(demo=False):
    if demo:
        data = [{'warehouseID': 507, 'warehouseName': 'Демо-склад', 'boxTypeID': 2,
                 'date': str(dt.datetime.now(dt.timezone.utc).date()), 'coefficient': 2,
                 'allowUnload': True}]
        source = 'demo'
    elif credential('WB_API_TOKEN'):
        try:
            data = request_json('https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients',
                                None, {'Authorization': credential('WB_API_TOKEN')})
            if not isinstance(data, list) or any('coefficient' not in r or 'warehouseID' not in r for r in data):
                raise RuntimeError('Неизвестный формат тарифов WB.')
            source = 'wb_api'
        except (RuntimeError, TypeError):
            with connect() as con:
                con.execute("INSERT INTO tariffs VALUES(1,'[]','','wb_api',?) ON CONFLICT(id) DO UPDATE SET error=excluded.error",
                    ('Тарифы WB не получены. Метод может быть отключён или недоступен для токена. Старые данные не используются как свежие.',))
            return
    else:
        return
    stamp = now()
    with connect() as con:
        previous = con.execute('SELECT payload,source FROM tariffs WHERE id=1').fetchone()
        if previous and previous['source'] == source and source != 'demo':
            old = {(r['warehouseID'],r.get('boxTypeID'),r.get('date')): r['coefficient'] for r in json.loads(previous['payload'])}
            profiles = [json.loads(r[0]) for r in con.execute('SELECT payload FROM profiles')]
            selected = {(p['warehouse_id'],p['box_type']) for p in profiles if p.get('warehouse_id')}
            for r in data:
                key = (r['warehouseID'],r.get('boxTypeID'),r.get('date'))
                if key[:2] in selected and key in old and old[key] != r['coefficient']:
                    message = f"WB: склад {r.get('warehouseName',key[0])}, дата {key[2]}, тип {key[1]}: коэффициент {old[key]} → {r['coefficient']}. Перепроверьте стоимость поставки."
                    con.execute("INSERT INTO alerts(product,message,created,status) VALUES('tariff',?,?,'pending')", (message,stamp))
        con.execute('INSERT OR REPLACE INTO tariffs VALUES(1,?,?,?,?)', (json.dumps(data),stamp,source,''))


def demo_products():
    values = [('Ozon', 'HOME-021', 'Органайзер для кухни', 8, 2, 3.2),
              ('WB', 'LINEN-044', 'Комплект постельного белья', 36, 4, 7),
              ('Ozon', 'CUP-008', 'Кружка керамическая', 0, 0, 2),
              ('WB', 'BAG-012', 'Сумка-шоппер', 124, 3, 4.5),
              ('Ozon', 'LIGHT-003', 'Настольная лампа', 64, 0, 0)]
    rows = [dict(id=f'demo:{i}', platform=p, offer=o, name=n, quantity=q, reserved=r, daily=d)
            for i, (p,o,n,q,r,d) in enumerate(values)]
    today = dt.datetime.now(dt.timezone.utc).date()
    for i, row in enumerate(rows):
        row.update(price=[1490,2990,690,590,2490][i],price_source='Демо · вымышленная цена',catalog_source='demo',
            dimensions={'height_cm':10+i,'width_cm':20,'length_cm':30,'volume_l':round((10+i)*.6,2)})
        history = []
        for n in range(1, 57):
            rate = row['daily']
            if i == 1 and n <= 3:
                rate *= 2
            history.append({'date': str(today-dt.timedelta(days=n)), 'units': rate})
        row['history'] = history
        row['daily'] = sum(r['units'] for r in history[:14]) / 14
    return rows


def seed_demo_profiles():
    with connect() as con:
        for p in demo_products():
            config = {**DEFAULTS, 'warehouse': 'Демо-склад', 'warehouse_id': 507 if p['platform']=='WB' else 0,
                'coefficient_now': 2, 'coefficient_wait': 0,
                'acceptance_min': 2 if p['platform']=='WB' else 1,
                'acceptance_max': 10 if p['platform']=='WB' else 2}
            con.execute('INSERT OR IGNORE INTO profiles VALUES(?,?)', (p['id'],json.dumps(config)))
            costs=dict(price=None,cost=900 if p['offer']=='CUP-008' else round(p['price']*.4),commission_pct=25,tax_pct=6,acquiring_pct=1,logistics=90,storage=10,returns=30,other=25)
            con.execute('INSERT OR IGNORE INTO economics VALUES(?,?)',(p['id'],json.dumps(costs)))


def enriched_plan(product, profile, tariffs=None):
    config = dict(profile) if profile else None
    tariff_source = 'manual'
    if config and product['platform'] == 'WB' and tariffs and tariffs.get('source') == 'wb_api' and not tariffs.get('error'):
        try:
            age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(tariffs['updated'])
            target_date = str(dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=config['preparation'] + config['transit']))
            if dt.timedelta(0) <= age <= dt.timedelta(hours=1):
                for row in json.loads(tariffs['payload']):
                    if (row['warehouseID'] == config['warehouse_id'] and row.get('boxTypeID') == config['box_type']
                            and row['date'][:10] == target_date and row.get('allowUnload')
                            and float(row['coefficient']) in (0,1)):
                        config['coefficient_now'] = float(row['coefficient'])
                        tariff_source = 'wb_api'
                        break
        except (KeyError, ValueError, TypeError):
            pass
    plan = forecast(product, config)
    plan['tariff_source'] = tariff_source
    return plan


def risk(product, threshold, days):
    quantity, daily = product['quantity'], product['daily']
    coverage = quantity / daily if daily and daily > 0 else None
    level = 'empty' if quantity == 0 else 'low' if quantity <= threshold else 'soon' if coverage is not None and coverage <= days else 'ok'
    return level, coverage


def save_snapshot(products, mode, warning=''):
    stamp = now()
    with connect() as con:
        threshold, days = con.execute('SELECT threshold,days FROM settings').fetchone()
        old_mode = con.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
        tariff_row = con.execute('SELECT * FROM tariffs WHERE id=1').fetchone()
        tariffs = dict(tariff_row) if tariff_row else None
        if old_mode and old_mode[0] != mode:
            con.execute('DELETE FROM states')
        con.execute('DELETE FROM products')
        for p in products:
            con.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?,?)',
                (p['id'], p['platform'], p['offer'], p['name'], p['quantity'], p['reserved'], p['daily'], stamp))
            con.execute('INSERT OR REPLACE INTO details VALUES(?,?)',
                (p['id'],json.dumps({k:p[k] for k in ('history','sku','price','price_min','price_max','price_source','dimensions','catalog_source') if k in p})))
            level, coverage = risk(p, threshold, days)
            profile = con.execute('SELECT payload FROM profiles WHERE product=?', (p['id'],)).fetchone()
            plan = enriched_plan(p, json.loads(profile[0]) if profile else None, tariffs)
            if level != 'empty' and plan.get('deadline_days', 1) <= 0:
                level = 'supply_due'
            # Never deliver an old warning after recovery or a change of risk level.
            previous = con.execute('SELECT level FROM states WHERE product=?', (p['id'],)).fetchone()
            if level == 'ok' or (previous and previous[0] != level):
                con.execute("UPDATE alerts SET status='obsolete' WHERE product=? AND status='pending' AND category='stock'", (p['id'],))
            if level != 'ok' and (not previous or previous[0] != level):
                suffix = f' Запас примерно на {coverage:.1f} дн. по темпу заказов.' if coverage is not None else ''
                if plan.get('configured') and 'gap_max' in plan:
                    suffix += f" {plan['advice']} До появления товара в продаже: {plan['lead_min']}–{plan['lead_max']} дн. Возможный перерыв: {plan['gap_min']:.1f}–{plan['gap_max']:.1f} дн."
                    a, b = plan['scenarios']
                    if a['total_min'] is not None and b['total_min'] is not None:
                        suffix += (f" Сценарная стоимость: сейчас {a['total_min']:.0f}–{a['total_max']:.0f} ₽;"
                                   f" ожидание {b['total_min']:.0f}–{b['total_max']:.0f} ₽. Будущий тариф — допущение. "
                                   + plan['comparison'])
                prefix = '[ДЕМО] ' if mode == 'demo' else ''
                message = f"{prefix}{p['platform']} · {p['offer']}: доступно {p['quantity']} шт.{suffix}"
                con.execute('INSERT INTO alerts(product,message,created,status) VALUES(?,?,?,?)',
                    (p['id'], message, stamp, 'demo' if mode == 'demo' else 'pending'))
            con.execute('INSERT OR REPLACE INTO states VALUES(?,?)', (p['id'], level))
            costs=con.execute('SELECT payload FROM economics WHERE product=?',(p['id'],)).fetchone()
            financial=calculate(json.loads(costs[0]) if costs else {},p.get('price'))
            loss=financial['status']=='loss'
            finance_key='profit:'+p['id']
            previous_loss=con.execute('SELECT level FROM states WHERE product=?',(finance_key,)).fetchone()
            if loss and (not previous_loss or previous_loss[0]!='loss'):
                message=f"{'[ДЕМО] ' if mode=='demo' else ''}{p['platform']} · {p['offer']}: по вашим расходам продажа убыточна. Цена {financial['price']:.2f} ₽, оценка результата {financial['profit']:.2f} ₽/шт. Проверьте цену, комиссии и расходы в разделе «Прибыль»."
                con.execute("INSERT INTO alerts(product,message,created,status,category) VALUES(?,?,?,?,'profit')",(p['id'],message,stamp,'demo' if mode=='demo' else 'pending'))
            if not loss:
                con.execute("UPDATE alerts SET status='obsolete' WHERE product=? AND category='profit' AND status='pending'",(p['id'],))
            con.execute('INSERT OR REPLACE INTO states VALUES(?,?)',(finance_key,'loss' if loss else financial['status']))
        con.execute("UPDATE alerts SET status='obsolete' WHERE status='pending' AND product!='tariff' AND product NOT IN (SELECT id FROM products)")
        for key, value in [('mode', mode), ('updated', stamp), ('error', ''), ('warning', warning)]:
            con.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key,value))


def deliver():
    if not is_owner() and not billing.access(DB,account_key())['allowed']:
        return
    token, chat = credential('TELEGRAM_BOT_TOKEN'), credential('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return
    with connect() as con:
        choice=con.execute("SELECT value FROM meta WHERE key='marketplace'").fetchone()
        marketplace=choice[0] if choice else ''
        rows = con.execute("SELECT a.* FROM alerts a LEFT JOIN products p ON a.product=p.id WHERE a.status='pending' AND a.attempts<5 AND (?='' OR p.platform=? OR (a.product='tariff' AND ?='WB')) ORDER BY a.id LIMIT 20",(marketplace,marketplace,marketplace)).fetchall()
    for row in rows:
        bot=workspace_config()['bot']
        with connect() as con:
            state_row=con.execute('SELECT level FROM states WHERE product=?',(row['product'],)).fetchone()
        kind='profit_loss' if row['category']=='profit' else 'tariff_change' if row['product']=='tariff' else 'supply_due' if state_row and state_row[0]=='supply_due' else 'low_stock'
        if not bot[kind]:
            with connect() as con:
                con.execute("UPDATE alerts SET status='muted' WHERE id=?",(row['id'],))
            continue
        try:
            markup={}
            if bot.get('app_url') and chat.isdigit():
                markup={'reply_markup':{'inline_keyboard':[[{'text':'Открыть приложение','web_app':{'url':bot['app_url']}}]]}}
            result = request_json(f'https://api.telegram.org/bot{token}/sendMessage',
                {'chat_id': chat, 'text': row['message'],**markup})
            if not result.get('ok'):
                raise RuntimeError('Telegram не подтвердил доставку.')
            status = 'sent'
        except RuntimeError:
            status = 'pending'
        with connect() as con:
            con.execute('UPDATE alerts SET status=?,attempts=attempts+1 WHERE id=?', (status,row['id']))
        if status == 'pending':
            break


def sync():
    global LAST_ATTEMPT
    lock=current_lock()
    if not lock.acquire(blocking=False):
        return False
    try:
        previous=LAST_ATTEMPT if is_owner() else USER_ATTEMPTS.get(account_key(),0)
        if time.monotonic() - previous < 65:
            return False
        if is_owner():LAST_ATTEMPT = time.monotonic()
        else:USER_ATTEMPTS[account_key()]=time.monotonic()
        if (credential('OZON_CLIENT_ID') and credential('OZON_API_KEY')) or credential('WB_API_TOKEN'):
            products, warnings = [], []
            if credential('OZON_CLIENT_ID') and credential('OZON_API_KEY'):
                rows, warning = ozon_products()
                warnings.extend(enrich_catalog(rows,'Ozon',request_json,{'Client-Id':credential('OZON_CLIENT_ID'),'Api-Key':credential('OZON_API_KEY')}))
                products.extend(rows)
                if warning:
                    warnings.append(warning)
            if credential('WB_API_TOKEN'):
                rows, warning = wb_products()
                warnings.extend(enrich_catalog(rows,'WB',request_json,{'Authorization':credential('WB_API_TOKEN')}))
                products.extend(rows)
                if warning:
                    warnings.append(warning)
            refresh_tariffs()
            save_snapshot(products, 'live', ' '.join(warnings))
            if products and not is_owner():billing.start_trial(DB,account_key())
            deliver()
        else:
            seed_demo_profiles()
            refresh_tariffs(demo=True)
            save_snapshot(demo_products(), 'demo')
        return True
    except Exception:
        with connect() as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES('error', ?)",
                ('Обновление не удалось. Показаны последние сохранённые данные. Проверьте API-доступ и соединение.',))
        return False
    finally:
        lock.release()


def state():
    with connect() as con:
        settings = dict(con.execute('SELECT threshold, days FROM settings').fetchone())
        tariff_row = con.execute('SELECT * FROM tariffs WHERE id=1').fetchone()
        tariffs = dict(tariff_row) if tariff_row else None
        products = []
        for row in con.execute('SELECT * FROM products'):
            p = dict(row)
            p['level'], p['coverage'] = risk(p, **settings)
            detail = con.execute('SELECT payload FROM details WHERE product=?', (p['id'],)).fetchone()
            if detail:
                p.update(json.loads(detail[0]))
            profile = con.execute('SELECT payload FROM profiles WHERE product=?', (p['id'],)).fetchone()
            p['profile'] = json.loads(profile[0]) if profile else None
            costs=con.execute('SELECT payload FROM economics WHERE product=?',(p['id'],)).fetchone()
            p['economics']=calculate(json.loads(costs[0]) if costs else {},p.get('price'))
            p['plan'] = enriched_plan(p,p['profile'],tariffs)
            # History is used on the server; don't resend 56 raw rows per item to a phone.
            p.pop('history',None)
            if p['level'] != 'empty' and p['plan'].get('deadline_days', 1) <= 0:
                p['level'] = 'supply_due'
            products.append(p)
        meta = dict(con.execute('SELECT key,value FROM meta').fetchall())
        marketplace = meta.get('marketplace', '')
        alerts = [dict(r) for r in con.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 50')]
        if marketplace in ('Ozon','WB'):
            products = [p for p in products if p['platform']==marketplace]
            ids = {p['id'] for p in products}
            alerts = [a for a in alerts if a['product'] in ids or (a['product']=='tariff' and marketplace=='WB')]
        if tariffs:
            tariffs.pop('payload',None)
        return dict(products=products, marketplace=marketplace, settings=settings, meta=meta, alerts=alerts, csrf=CSRF, tariffs=tariffs if marketplace!='Ozon' else None,
        busy=current_lock().locked(), telegram=bool(credential('TELEGRAM_BOT_TOKEN') and credential('TELEGRAM_CHAT_ID')),
        workspace=workspace_config(),connections=connection_status(),account=dict(owner=is_owner(),demo=is_demo_account(),web=bool(ACTIVE_USER.get() and ACTIVE_USER.get().startswith('web:'))),subscription=billing.access(DB,account_key(),is_owner()))


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def handle_one_request(self):
        self.asset_cache=False
        self.versioned_asset=False
        self.session_cookie=None
        self.telegram_user=None
        return super().handle_one_request()

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *args):
        pass

    def respond(self, code, body, kind='application/json; charset=utf-8', download=None):
        content = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else body
        cached=getattr(self,'asset_cache',False)
        tag='"'+hashlib.sha256(content).hexdigest()+'"' if cached else None
        if cached and self.headers.get('If-None-Match')==tag:
            self.send_response(304);self.send_header('ETag',tag);self.send_header('Cache-Control','public, max-age=3600');self.send_header('Content-Length','0');self.end_headers();return
        compressed='gzip' in self.headers.get('Accept-Encoding','') and len(content)>800 and (kind.startswith(('text/','application/json','image/svg')))
        if compressed:content=gzip.compress(content,compresslevel=5)
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if cached and self.versioned_asset else 'public, max-age=3600' if cached else 'no-store')
        if tag:self.send_header('ETag',tag)
        if compressed:self.send_header('Content-Encoding','gzip')
        self.send_header('Vary','Accept-Encoding')
        self.send_header('X-Content-Type-Options', 'nosniff')
        sdk=" https://telegram.org" if getattr(self,'miniapp_gateway',False) else ''
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'"+sdk+"; style-src 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; form-action 'self'; base-uri 'self'")
        self.send_header('Referrer-Policy','no-referrer')
        if getattr(self,'session_cookie',None):self.send_header('Set-Cookie',self.session_cookie)
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="'+download+'"')
        self.end_headers()
        self.wfile.write(content)

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        self.versioned_asset=bool(urlsplit(self.path).query)
        self.path=urlsplit(self.path).path
        if not self.valid_host():
            return self.respond(403, {'error': 'Недопустимый адрес'})
        if self.path == '/api/state':
            return self.respond(200, state())
        if self.path=='/miniapp':
            content=(ROOT/'static'/'index.html').read_text(encoding='utf-8')
            content=content.replace('<script src="/app.js" defer>',
                '<script src="/telegram-web-app.js" defer></script><script src="/miniapp.js" defer></script><script src="/app.js" defer>',1)
            content=content.replace('<footer>',(ROOT/'static'/'studio.html').read_text(encoding='utf-8')+'<footer>',1)
            return self.respond(200,version_assets(content).encode(),'text/html; charset=utf-8')
        if self.path == '/site-preview':
            return self.respond(200,render_site(workspace_config()['site']).encode(),'text/html; charset=utf-8')
        if self.path == '/site-export.zip':
            return self.respond(200,site_archive(workspace_config()['site'],ROOT),'application/zip','zapas-site.zip')
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/miniapp.js': ('miniapp.js','text/javascript; charset=utf-8'),'/style.css': ('style.css', 'text/css; charset=utf-8'), '/studio.js': ('studio.js','text/javascript; charset=utf-8'),'/site.css': ('site.css','text/css; charset=utf-8'), '/manrope.ttf': ('manrope.ttf', 'font/ttf')}
        files['/economics.js']=('economics.js','text/javascript; charset=utf-8')
        files['/launch.js']=('launch.js','text/javascript; charset=utf-8')
        files['/hints.js']=('hints.js','text/javascript; charset=utf-8')
        files['/telegram-web-app.js']=('telegram-web-app.js','text/javascript; charset=utf-8')
        files['/subscription.js']=('subscription.js','text/javascript; charset=utf-8')
        files['/logo.svg'] = ('logo.svg','image/svg+xml')
        if self.path not in files:
            return self.respond(404, {'error': 'Не найдено'})
        name, kind = files[self.path]
        content=(ROOT / 'static' / name).read_bytes()
        if name=='index.html':
            content=content.replace(b'<footer>',(ROOT/'static'/'studio.html').read_bytes()+b'<footer>',1)
            content=version_assets(content.decode()).encode()
        else:self.asset_cache=True
        self.respond(200, content, kind)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-CSRF-Token') != CSRF:
            return self.respond(403, {'error': 'Обновите страницу'})
        if self.path=='/api/telegram-link':
            if is_owner():return self.respond(400,dict(error='Telegram владельца уже подключён.'))
            token=accounts.link_token(DB,account_key())
            with closing(sqlite3.connect(DB)) as con:row=con.execute("SELECT value FROM meta WHERE key='bot_username'").fetchone()
            if not row:return self.respond(400,dict(error='Общий бот ещё не настроен администратором.'))
            return self.respond(200,dict(url='https://t.me/'+row[0]+'?start=link_'+token))
        if self.path=='/api/billing/create-payment':
            try:
                if getattr(self,'miniapp_gateway',False) and self.headers.get('X-Telegram-Init-Data'):
                    raise ValueError('Покупка по СБП не предлагается внутри Telegram.')
                origin=os.getenv('ZAPAS_WEB_ORIGIN','').rstrip('/')
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=1024:raise ValueError('Проверьте условия покупки.')
                data=json.loads(self.rfile.read(size))
                result=billing.create_order(DB,account_key(),billing.merchant_request,origin+'/web-app#subscription',data.get('accept_terms') is True)
                return self.respond(200,result)
            except (ValueError,RuntimeError,TypeError,AttributeError) as error:return self.respond(400,dict(error=str(error)))
        if self.path=='/api/billing/check-payment':
            try:
                if not account_key().startswith('web:'):raise ValueError('Проверка доступна в своём веб-кабинете.')
                with billing.registry(DB) as con:
                    rows=con.execute("SELECT remote_id FROM purchases WHERE user=? AND remote_id IS NOT NULL AND status IN ('pending','waiting_for_capture') ORDER BY created DESC LIMIT 3",(account_key(),)).fetchall()
                for row in rows:
                    verified=billing.merchant_request('GET','/payments/'+row['remote_id'])
                    billing.apply_payment(DB,verified)
                return self.respond(200,dict(subscription=billing.access(DB,account_key()),csrf=CSRF))
            except (ValueError,RuntimeError) as error:return self.respond(400,dict(error=str(error)))
        if not is_owner() and self.path in ('/api/telegram-menu','/api/telegram-discover'):
            return self.respond(403,dict(error='Настройка общего бота доступна только владельцу.'))
        if self.path=='/api/telegram-menu':
            token,chat=credential('TELEGRAM_BOT_TOKEN'),credential('TELEGRAM_CHAT_ID')
            config=workspace_config()['bot']
            if not token or not chat or not config.get('app_url') or not chat.isdigit():
                return self.respond(400,dict(error='Добавьте токен, положительный ID личного чата и HTTPS-адрес /miniapp.'))
            try:
                result=request_json(f'https://api.telegram.org/bot{token}/setChatMenuButton',
                    {'chat_id':int(chat),'menu_button':{'type':'web_app','text':'Открыть приложение','web_app':{'url':config['app_url']}}})
                if not result.get('ok'):
                    raise RuntimeError('Telegram не подтвердил кнопку.')
                return self.respond(200,dict(ok=True,message='Кнопка приложения настроена в вашем личном чате с ботом.'))
            except RuntimeError as exc:
                return self.respond(400,dict(error=str(exc)))
        if self.path in ('/api/workspace','/api/connections','/api/bot-preview','/api/telegram-discover','/api/marketplace'):
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=20000:
                    raise ValueError('Слишком большой запрос.')
                data=json.loads(self.rfile.read(size))
                if self.path=='/api/marketplace':
                    if not isinstance(data,dict) or set(data)!={'marketplace'} or data['marketplace'] not in ('Ozon','WB'):
                        raise ValueError('Выберите Ozon или Wildberries.')
                    with connect() as con:
                        con.execute("INSERT OR REPLACE INTO meta VALUES('marketplace',?)",(data['marketplace'],))
                    return self.respond(200,dict(ok=True))
                if self.path=='/api/telegram-discover':
                    token=data.get('token','')
                    if not isinstance(token,str) or not token or len(token)>4096 or not all(c.isalnum() or c in ':_-' for c in token):
                        raise ValueError('Введите токен своего бота.')
                    info=request_json(f'https://api.telegram.org/bot{token}/getWebhookInfo',{})
                    if not info.get('ok') or info.get('result',{}).get('url'):
                        raise ValueError('Бот использует другое подключение. Оно не изменено.')
                    updates=request_json(f'https://api.telegram.org/bot{token}/getUpdates',{'timeout':0,'allowed_updates':['message']})
                    if not updates.get('ok'):
                        raise ValueError('Telegram не подтвердил чтение сообщений.')
                    chats={}
                    for update in updates.get('result',[]):
                        message=update.get('message',{})
                        chat=message.get('chat',{})
                        if (chat.get('type')=='private' and message.get('text','').split('@')[0]=='/start'
                                and time.time()-message.get('date',0)<600):
                            chats[str(chat['id'])]=True
                    if len(chats)!=1:
                        raise ValueError('Нажмите Start в своём боте. Для автоматического определения нужен один личный чат за последние 10 минут.')
                    return self.respond(200,dict(chat_id=next(iter(chats))))
                if self.path=='/api/connections':
                    return self.respond(200,configure_connection(data))
                if self.path=='/api/bot-preview':
                    config=validate_bot(data['config'])
                    if not isinstance(data['text'],str) or len(data['text'])>1000:
                        raise ValueError('Слишком длинное сообщение.')
                    return self.respond(200,dict(reply=bot_reply(data['text'],config,state())))
                if not isinstance(data,dict) or set(data)!={'kind','config'} or data['kind'] not in ('site','bot'):
                    raise ValueError('Неизвестный конструктор.')
                config=validate_site(data['config']) if data['kind']=='site' else validate_bot(data['config'])
                with connect() as con:
                    con.execute('INSERT OR REPLACE INTO workspace VALUES(?,?)',(data['kind'],json.dumps(config)))
                return self.respond(200,dict(ok=True))
            except (ValueError,TypeError,KeyError,RuntimeError) as exc:
                return self.respond(400,{'error':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'Проверьте поля формы.'})
        if self.path == '/api/sync':
            if current_lock().locked():
                return self.respond(409, {'error': 'Обновление уже выполняется'})
            threading.Thread(target=contextvars.copy_context().run, args=(sync,), daemon=True).start()
            return self.respond(202, {'ok': True})
        if self.path == '/api/profile':
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 8192:
                    raise ValueError('Слишком большой запрос.')
                data = json.loads(self.rfile.read(size))
                product = data['product']
                if not isinstance(product, str) or len(product) > 200:
                    raise ValueError('Некорректный товар.')
                config = validate_profile(data['profile'])
                with connect() as con:
                    if not con.execute('SELECT id FROM products WHERE id=?', (product,)).fetchone():
                        raise ValueError('Товар не найден.')
                    con.execute('INSERT OR REPLACE INTO profiles VALUES(?,?)', (product,json.dumps(config)))
                return self.respond(200, {'ok': True})
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond(400, {'error': str(exc) if isinstance(exc, ValueError) else 'Проверьте параметры маршрута.'})
        if self.path=='/api/economics':
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=8192:raise ValueError('Слишком большой запрос.')
                data=json.loads(self.rfile.read(size))
                product=data['product'];costs=validate_economics(data['costs'])
                if not isinstance(product,str):raise ValueError('Выберите товар.')
                with connect() as con:
                    if not con.execute('SELECT 1 FROM products WHERE id=?',(product,)).fetchone():raise ValueError('Товар не найден.')
                    con.execute('INSERT OR REPLACE INTO economics VALUES(?,?)',(product,json.dumps(costs)))
                return self.respond(200,dict(ok=True))
            except (ValueError,KeyError,TypeError):
                return self.respond(400,dict(error='Проверьте суммы расходов, проценты и выбранный товар.'))
        if self.path == '/api/settings':
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 1024:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                threshold, days = data['threshold'], data['days']
                if type(threshold) is not int or type(days) is not int or not 0 <= threshold <= 100000 or not 1 <= days <= 90:
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                return self.respond(400, {'error': 'Порог: 0–100000 шт.; запас: 1–90 дней.'})
            with connect() as con:
                con.execute('UPDATE settings SET threshold=?,days=? WHERE id=1', (threshold, days))
            return self.respond(200, {'ok': True})
        self.respond(404, {'error': 'Не найдено'})


def worker():
    while True:
        time.sleep(900)
        sync()
        with accounts.store(DB) as con:users=[r[0] for r in con.execute('SELECT id FROM web_accounts')]
        for user in users:
            if not billing.access(DB,user)['allowed']:continue
            with account_scope(user):
                if credential('WB_API_TOKEN') or credential('OZON_API_KEY'):sync()


class MiniAppHandler(Handler):
    """Separate listener: never put the unprotected local admin server behind a tunnel."""
    miniapp_gateway=True

    def valid_host(self):
        # The public listener belongs to the service, never to an individual workspace.
        with closing(sqlite3.connect(DB)) as con:
            row=con.execute("SELECT payload FROM workspace WHERE key='bot'").fetchone()
        public=urlsplit(json.loads(row[0]).get('app_url','') if row else '').netloc
        website=urlsplit(os.getenv('ZAPAS_WEB_ORIGIN','')).netloc
        return self.headers.get('Host') in {public,website,f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}- {''}

    def web_session(self):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            token=cookie['zapas_session'].value if 'zapas_session' in cookie else ''
            self.web_token=token
            return accounts.session(DB,token) if token else None
        except (ValueError,KeyError):return None

    def authenticated(self):
        try:
            verify_init_data(self.headers.get('X-Telegram-Init-Data',''),credential('TELEGRAM_BOT_TOKEN'),credential('TELEGRAM_CHAT_ID'))
            return True
        except (ValueError,TypeError) as owner_error:
            reason=str(owner_error) if isinstance(owner_error,ValueError) else 'Не удалось проверить вход Telegram.'
            try:
                user=verify_registered_user(self.headers.get('X-Telegram-Init-Data',''),credential('TELEGRAM_BOT_TOKEN'))
                if accounts.has_demo(DB,'tg:'+str(user)):
                    self.telegram_user='tg:'+str(user)
                    return True
                reason='Сначала отправьте боту /start, затем откройте приложение новой кнопкой. Так будет создан ваш отдельный демо-магазин.'
            except (ValueError,TypeError) as error:
                if self.headers.get('X-Telegram-Init-Data') and isinstance(error,ValueError):reason=str(error)
            self.respond(401,dict(error=reason))
            return False

    def do_GET(self):
        path=urlsplit(self.path).path
        if not self.valid_host():return self.respond(403,dict(error='Недопустимый адрес'))
        if path=='/healthz':
            return self.respond(200,dict(status='ok'))
        if path in ('/','/auth.js'):
            name,kind=('web.html','text/html; charset=utf-8') if path=='/' else ('auth.js','text/javascript; charset=utf-8')
            return self.respond(200,(ROOT/'static'/name).read_bytes(),kind)
        if path=='/web-app':
            content=(ROOT/'static'/'index.html').read_text(encoding='utf-8').replace('<footer>',(ROOT/'static'/'studio.html').read_text(encoding='utf-8')+'<footer>',1)
            return self.respond(200,version_assets(content).encode(),'text/html; charset=utf-8')
        assets={'/logo.svg','/miniapp','/app.js','/miniapp.js','/studio.js','/style.css','/site.css','/manrope.ttf','/economics.js','/subscription.js','/launch.js','/hints.js','/telegram-web-app.js'}
        if path in assets:return super().do_GET()
        session=self.web_session()
        if not session:
            if not self.authenticated():return
            with account_scope(self.telegram_user):
                if path=='/api/access':return self.respond(200,dict(csrf=CSRF,subscription=billing.access(DB,account_key(),is_owner())))
                return super().do_GET()
        with account_scope(session['user']):
            access=billing.access(DB,account_key())
            if path=='/api/access':return self.respond(200,dict(csrf=CSRF,subscription=access,account={'owner':False,'web':True}))
            if not access['allowed']:return self.respond(402,dict(error='Пробный или оплаченный период закончился.',subscription=access,csrf=CSRF))
            return super().do_GET()

    def do_POST(self):
        if not self.valid_host():return self.respond(403,dict(error='Недопустимый адрес'))
        if self.path.startswith('/api/auth/'):
            expected=('http://' if self.headers.get('Host','').startswith(('127.0.0.1:','localhost:')) else 'https://')+self.headers.get('Host','')
            if self.headers.get('Origin')!=expected:return self.respond(403,dict(error='Откройте форму входа на сайте.'))
            try:
                if self.path=='/api/auth/logout':
                    session=self.web_session()
                    if session:accounts.logout(DB,self.web_token)
                    self.session_cookie='zapas_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
                    return self.respond(200,dict(ok=True))
                if self.path not in ('/api/auth/login','/api/auth/register'):raise ValueError('Неизвестное действие.')
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=2048:raise ValueError('Проверьте поля входа.')
                accounts.throttle(DB,self.client_address[0])
                data=json.loads(self.rfile.read(size))
                result=accounts.login(DB,data['login'],data['password'],self.path.endswith('/register'))
                secure='; Secure' if expected.startswith('https:') else ''
                self.session_cookie='zapas_session='+result['token']+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800'+secure
                return self.respond(200,dict(ok=True))
            except (ValueError,KeyError,TypeError):return self.respond(400,dict(error='Не удалось войти. Логин: 3–48 латинских букв/цифр. Пароль: 12–128 символов. Проверьте данные или попробуйте позже.'))
        if self.path=='/api/billing/webhook':
            if os.getenv('ZAPAS_SBP_ENABLED')!='1':return self.respond(503,dict(error='Приём платежей не включён.'))
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=8192:raise ValueError()
                data=json.loads(self.rfile.read(size));remote_id=data['object']['id']
                if not isinstance(remote_id,str) or len(remote_id)>100 or not all(c.isalnum() or c=='-' for c in remote_id):raise ValueError()
                verified=billing.merchant_request('GET','/payments/'+remote_id)
                billing.apply_payment(DB,verified)
                return self.respond(200,dict(ok=True))
            except (ValueError,KeyError,TypeError):return self.respond(400,dict(error='Платёж не подтверждён.'))
            except RuntimeError:return self.respond(502,dict(error='Проверка временно недоступна.'))
        session=self.web_session()
        if not session:
            if self.authenticated():
                with account_scope(self.telegram_user):
                    if is_demo_account() and self.path not in ('/api/marketplace','/api/economics','/api/profile','/api/settings','/api/bot-preview'):
                        return self.respond(403,dict(error='Это демо с вымышленными товарами. Подключение ключей, публикация и оплата отключены.'))
                    return super().do_POST()
            return
        with account_scope(session['user']):
            access=billing.access(DB,account_key())
            if not access['allowed'] and self.path not in ('/api/billing/create-payment','/api/billing/check-payment'):
                return self.respond(402,dict(error='Доступ закончился.',subscription=access))
            if self.path=='/api/workspace':
                # A web customer cannot configure a paid Mini App or the shared bot's launch URL.
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=20000:return self.respond(400,dict(error='Слишком большой запрос.'))
                raw=self.rfile.read(size)
                import io
                self.rfile=io.BytesIO(raw)
                try:
                    data=json.loads(raw)
                    if data.get('kind')=='bot' and data.get('config',{}).get('app_url'):
                        return self.respond(400,dict(error='Уведомления веб-версии работают в чате; платное приложение внутри Telegram не подключается.'))
                except (ValueError,AttributeError):return self.respond(400,dict(error='Проверьте настройки.'))
            return super().do_POST()


def bot_worker():
    active_token=None
    offset=0
    started=int(time.time())
    while True:
        token,chat=credential('TELEGRAM_BOT_TOKEN'),credential('TELEGRAM_CHAT_ID')
        config=workspace_config()['bot']
        if not token or not chat or not config['enabled']:
            time.sleep(3)
            continue
        try:
            fingerprint=hashlib.sha256(token.encode()).hexdigest()[:16]
            key='bot_offset_'+fingerprint
            if token!=active_token:
                info=request_json(f'https://api.telegram.org/bot{token}/getWebhookInfo',{})
                if not info.get('ok') or info.get('result',{}).get('url'):
                    raise RuntimeError('Бот занят другим подключением.')
                with connect() as con:
                    saved=con.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                offset=int(saved[0]) if saved else -1
                active_token=token
            result=request_json(f'https://api.telegram.org/bot{token}/getUpdates',
                {'offset':offset,'timeout':20,'allowed_updates':['message']})
            if not result.get('ok'):
                raise RuntimeError('Telegram не подтвердил обновления.')
            for update in result.get('result',[]):
                offset=int(update['update_id'])+1
                message=update.get('message',{})
                incoming=str(message.get('chat',{}).get('id',''))
                incoming_text=message.get('text','')
                private=message.get('chat',{}).get('type')=='private'
                fresh=message.get('date',0)>=started
                if private and fresh and isinstance(incoming_text,str) and incoming!=chat:
                    reply_text=None
                    if incoming_text.startswith('/start link_'):
                        try:
                            user=accounts.bind_chat(DB,incoming_text.split('link_',1)[1].strip(),incoming,chat)
                            with account_scope(user):
                                with connect() as con:
                                    con.execute("INSERT OR REPLACE INTO meta VALUES('linked_chat',?)",(incoming,))
                                    config=workspace_config()['bot'];config['enabled']=True;config['app_url']=''
                                    con.execute("INSERT OR REPLACE INTO workspace VALUES('bot',?)",(json.dumps(config),))
                            reply_text='Telegram привязан к вашему веб-кабинету. Здесь доступны обзор, остатки, поставки и уведомления. /summary — проверить свой магазин.'
                        except ValueError as error:reply_text=str(error)
                    user=accounts.user_for_chat(DB,incoming)
                    first_command=incoming_text.strip().split()[0].split('@')[0].lower() if incoming_text.strip() else ''
                    if not user and first_command in ('/start','/help') and not incoming_text.startswith('/start link_'):
                        accounts.register_demo(DB,incoming)
                    if user:
                        with account_scope(user):
                            config=workspace_config()['bot'];config['app_url']=''
                            if not reply_text:
                                access=billing.access(DB,user)
                                info_command=incoming_text.strip().split()[0].split('@')[0].lower() if incoming_text.strip() else ''
                                if access['allowed']:
                                    reply_text=bot_reply(incoming_text,config,state())
                                elif info_command in ('/paysupport','/support','/terms','/subscription','/subscribe','/help') or incoming_text in ('Мой доступ',config['help_label']):
                                    reply_text=bot_reply(incoming_text,config,{'account':{'web':True},'subscription':access})
                                else:reply_text='Срок доступа к веб-кабинету закончился. Данные сохранены. /paysupport — информация о поддержке.'
                            if config['enabled']:
                                reply=request_json(f'https://api.telegram.org/bot{token}/sendMessage',{'chat_id':incoming,'text':reply_text[:4000],'reply_markup':bot_keyboard(config)})
                                if not reply.get('ok'):raise RuntimeError('Ответ не подтверждён.')
                    elif accounts.has_demo(DB,'tg:'+incoming):
                        demo_user='tg:'+incoming
                        public_app=workspace_config()['bot'].get('app_url','')
                        with account_scope(demo_user):
                            config=workspace_config()['bot'];config['app_url']=public_app
                            if first_command=='/start':
                                reply_text='Добро пожаловать в «Запас»! 📦\n\nЗдесь можно проверить остатки, сроки поставки и доход с товара. Нажмите «Открыть приложение» и выберите Ozon или Wildberries.\n\nДля знакомства у вас отдельный демо-магазин с вымышленными товарами. Чужие ключи и данные недоступны. Подключение настоящих магазинов и оплата в этом демо выключены.\n\n/help — инструкция, /summary — обзор, /stock — остатки.'
                            else:reply_text=bot_reply(incoming_text,config,state())
                            reply=request_json(f'https://api.telegram.org/bot{token}/sendMessage',{'chat_id':incoming,'text':reply_text[:4000],'reply_markup':bot_keyboard(config)})
                            if not reply.get('ok'):raise RuntimeError('Ответ не подтверждён.')
                    elif reply_text:
                        request_json(f'https://api.telegram.org/bot{token}/sendMessage',{'chat_id':incoming,'text':reply_text[:4000]})
                    elif first_command:
                        request_json(f'https://api.telegram.org/bot{token}/sendMessage',{'chat_id':incoming,'text':'Нажмите /start, чтобы открыть своё демо приложения «Запас».'})
                # Never reveal seller data to another chat or reply to old requests.
                if (str(message.get('chat',{}).get('id'))==chat and message.get('date',0)>=started
                        and isinstance(message.get('text'),str)):
                    config=workspace_config()['bot']
                    if config['enabled']:
                        text=bot_reply(message['text'],config,state())
                        reply=request_json(f'https://api.telegram.org/bot{token}/sendMessage',
                            {'chat_id':chat,'text':text[:4000],'reply_markup':bot_keyboard(config)})
                        if not reply.get('ok'):
                            raise RuntimeError('Ответ не подтверждён.')
                with connect() as con:
                    con.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(offset)))
            with connect() as con:
                con.execute("INSERT OR REPLACE INTO meta VALUES('bot_status','Ожидает сообщения в разрешённом чате')")
        except (RuntimeError,ValueError,KeyError,TypeError):
            active_token=None
            with connect() as con:
                con.execute("INSERT OR REPLACE INTO meta VALUES('bot_status','Нет связи с Telegram или бот занят другим подключением')")
            time.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--miniapp-port',type=int,default=8766)
    parser.add_argument('--public-host',default='127.0.0.1')
    args = parser.parse_args()
    from deployment import validate_environment, configure_public_app
    validate_environment()
    init()
    restore_credentials()
    configure_public_app(DB)
    # Never show demo data as a successful live connection.
    if (credential('OZON_CLIENT_ID') and credential('OZON_API_KEY')) or credential('WB_API_TOKEN'):
        with connect() as con:
            previous_mode = con.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
            if not previous_mode or previous_mode[0] != 'live':
                con.execute('DELETE FROM products')
                con.execute('DELETE FROM states')
                con.execute("DELETE FROM meta WHERE key IN ('updated','error','warning')")
            con.execute("INSERT OR REPLACE INTO meta VALUES('mode','live')")
        threading.Thread(target=contextvars.copy_context().run, args=(sync,), daemon=True).start()
    else:
        seed_demo_profiles()
        refresh_tariffs(demo=True)
        save_snapshot(demo_products(), 'demo')
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=bot_worker, daemon=True).start()
    gateway=ThreadingHTTPServer((args.public_host,args.miniapp_port),MiniAppHandler)
    threading.Thread(target=gateway.serve_forever,daemon=True).start()
    print(f'Telegram Mini App gateway: http://127.0.0.1:{args.miniapp_port}/miniapp (requires signed Telegram login)',flush=True)
    print(f'Запас: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
