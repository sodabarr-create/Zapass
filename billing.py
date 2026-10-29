"""Per-account trial and one-off RUB/SBP access. No browser-side grants."""
from contextlib import contextmanager
import datetime as dt
import json
import os
import secrets
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

TRIAL_DAYS=7
PERIOD_DAYS=30
# A proposal, not an approved selling price. Checkout also requires merchant credentials.
PRICE_RUB=299

def configured_price():
    try:
        value=int(os.getenv('ZAPAS_PRICE_RUB',str(PRICE_RUB)))
        return value if 1<=value<=100000 else PRICE_RUB
    except ValueError:return PRICE_RUB

def checkout_ready():
    urls=[urlsplit(os.getenv(k,'')) for k in ('ZAPAS_WEB_ORIGIN','ZAPAS_TERMS_URL','ZAPAS_SUPPORT_URL')]
    return (os.getenv('ZAPAS_SBP_ENABLED')=='1' and os.getenv('ZAPAS_PRICE_APPROVED')=='1'
        and all(os.getenv(k) for k in ('YOOKASSA_SHOP_ID','YOOKASSA_SECRET_KEY'))
        and all(u.scheme=='https' and u.hostname and not u.username and not u.password for u in urls))

@contextmanager
def registry(path):
    con=sqlite3.connect(path,timeout=20);con.row_factory=sqlite3.Row
    try:
        with con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS access_accounts(user TEXT PRIMARY KEY, trial_started INTEGER, paid_until INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS purchases(id TEXT PRIMARY KEY,user TEXT NOT NULL,amount INTEGER NOT NULL,created INTEGER NOT NULL,remote_id TEXT UNIQUE,status TEXT DEFAULT 'created',credited INTEGER DEFAULT 0,checkout_url TEXT);
            CREATE TABLE IF NOT EXISTS purchase_terms(order_id TEXT PRIMARY KEY,version TEXT NOT NULL,accepted INTEGER NOT NULL);
            ''')
            yield con
    finally:con.close()

def access(path,user,owner=False,clock=None):
    now=int(time.time() if clock is None else clock)
    if owner:return dict(status='owner',allowed=True,days_left=None,trial_days=TRIAL_DAYS,period_days=PERIOD_DAYS,price_rub=PRICE_RUB,checkout_enabled=False)
    with registry(path) as con:
        con.execute('INSERT OR IGNORE INTO access_accounts(user) VALUES(?)',(user,))
        row=con.execute('SELECT * FROM access_accounts WHERE user=?',(user,)).fetchone()
    trial_end=(row['trial_started']+TRIAL_DAYS*86400) if row['trial_started'] is not None else 0
    until=max(trial_end,row['paid_until'])
    status='paid' if row['paid_until']>now else 'trial' if trial_end>now else 'pending' if row['trial_started'] is None else 'expired'
    return dict(status=status,allowed=status!='expired',trial_started=row['trial_started'],trial_until=trial_end or None,paid_until=row['paid_until'] or None,
        days_left=max(0,(until-now+86399)//86400),trial_days=TRIAL_DAYS,period_days=PERIOD_DAYS,price_rub=configured_price(),
        checkout_enabled=bool(checkout_ready()),terms_url=os.getenv('ZAPAS_TERMS_URL',''),support_url=os.getenv('ZAPAS_SUPPORT_URL',''),terms_version=os.getenv('ZAPAS_TERMS_VERSION','v1'))

def start_trial(path,user,clock=None):
    now=int(time.time() if clock is None else clock)
    with registry(path) as con:
        con.execute('INSERT OR IGNORE INTO access_accounts(user) VALUES(?)',(user,))
        con.execute('UPDATE access_accounts SET trial_started=? WHERE user=? AND trial_started IS NULL',(now,user))

def create_order(path,user,request,return_url,accepted_terms=False):
    if not access(path,user)['checkout_enabled']:
        raise ValueError('Оплата ещё не подключена. Цена не утверждена; деньги не списываются.')
    if not user.startswith('web:'):
        raise ValueError('СБП доступна только для самостоятельной веб-версии, не для покупки цифрового доступа внутри Telegram.')
    if accepted_terms is not True:raise ValueError('Перед оплатой подтвердите, что ознакомились с условиями покупки.')
    parsed=urlsplit(return_url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username:raise ValueError('Нужен постоянный HTTPS-адрес сайта.')
    with registry(path) as con:
        # Repeated button clicks reuse a recent unpaid order and the same idempotency key.
        con.execute('BEGIN IMMEDIATE')
        old=con.execute("SELECT * FROM purchases WHERE user=? AND created>? AND status IN ('created','pending','waiting_for_capture') ORDER BY created DESC LIMIT 1",(user,int(time.time())-600)).fetchone()
        if old:order=dict(old)
        else:
            order=dict(id=str(uuid.uuid4()),user=user,amount=configured_price(),created=int(time.time()))
            con.execute('INSERT INTO purchases(id,user,amount,created) VALUES(:id,:user,:amount,:created)',order)
        con.execute('INSERT OR IGNORE INTO purchase_terms VALUES(?,?,?)',(order['id'],os.getenv('ZAPAS_TERMS_VERSION','v1'),int(time.time())))
    if order.get('checkout_url'):return dict(id=order['id'],url=order['checkout_url'])
    body=dict(amount={'value':f"{order['amount']}.00",'currency':'RUB'},payment_method_data={'type':'sbp'},capture=True,
        confirmation={'type':'redirect','return_url':return_url},description='Запас · веб-доступ на 30 дней',metadata={'order_id':order['id'],'user':user})
    remote=request('POST','/payments',body,order['id'])
    url=remote.get('confirmation',{}).get('confirmation_url','')
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or not remote.get('id'):raise RuntimeError('Платёжный сервис не подтвердил ссылку.')
    with registry(path) as con:
        con.execute('UPDATE purchases SET remote_id=?,status=?,checkout_url=? WHERE id=?',(remote['id'],remote['status'],url,order['id']))
    return dict(id=order['id'],url=url)

def apply_payment(path,remote,clock=None):
    """Call only with a payment retrieved server-to-server from the merchant API."""
    now=int(time.time() if clock is None else clock)
    with registry(path) as con:
        con.execute('BEGIN IMMEDIATE')
        order=con.execute('SELECT * FROM purchases WHERE remote_id=?',(remote.get('id'),)).fetchone()
        if not order:raise ValueError('Неизвестный платёж.')
        meta=remote.get('metadata',{});amount=remote.get('amount',{})
        if meta.get('order_id')!=order['id'] or meta.get('user')!=order['user'] or amount.get('currency')!='RUB' or amount.get('value')!=f"{order['amount']}.00":
            raise ValueError('Данные платежа не совпадают с заказом.')
        if remote.get('status')!='succeeded' or remote.get('paid') is not True:
            if remote.get('status') in ('pending','waiting_for_capture','canceled'):
                con.execute('UPDATE purchases SET status=? WHERE id=? AND credited=0',(remote['status'],order['id']))
            return False
        if order['credited']:return False
        con.execute('INSERT OR IGNORE INTO access_accounts(user) VALUES(?)',(order['user'],))
        account=con.execute('SELECT * FROM access_accounts WHERE user=?',(order['user'],)).fetchone()
        trial_end=(account['trial_started']+TRIAL_DAYS*86400) if account['trial_started'] is not None else 0
        until=max(now,account['paid_until'],trial_end)+PERIOD_DAYS*86400
        con.execute('UPDATE access_accounts SET paid_until=? WHERE user=?',(until,order['user']))
        con.execute("UPDATE purchases SET credited=1,status='succeeded' WHERE id=?",(order['id'],))
        return True

def merchant_request(method,path,body=None,key=None):
    import base64,urllib.request,urllib.error
    if path!='/payments' and not (path.startswith('/payments/') and all(c.isalnum() or c in '-_' for c in path[len('/payments/'):])):
        raise ValueError('Неверный запрос платежа.')
    credentials=(os.environ['YOOKASSA_SHOP_ID']+':'+os.environ['YOOKASSA_SECRET_KEY']).encode()
    headers={'Authorization':'Basic '+base64.b64encode(credentials).decode(),'Content-Type':'application/json'}
    if key:headers['Idempotence-Key']=key
    req=urllib.request.Request('https://api.yookassa.ru/v3'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
    except (urllib.error.URLError,TimeoutError,ValueError):raise RuntimeError('Платёжный сервис недоступен. Не повторяйте оплату, пока не проверен статус.') from None
