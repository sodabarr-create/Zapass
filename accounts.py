"""Standalone website accounts and server sessions for isolated pilot workspaces."""
from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

@contextmanager
def store(path):
    con=sqlite3.connect(path,timeout=20);con.row_factory=sqlite3.Row
    try:
        with con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS web_accounts(id TEXT PRIMARY KEY,login TEXT UNIQUE NOT NULL,salt BLOB NOT NULL,password BLOB NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS web_sessions(hash TEXT PRIMARY KEY,user TEXT NOT NULL,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS auth_attempts(ip TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS telegram_links(hash TEXT PRIMARY KEY,user TEXT NOT NULL,expires INTEGER NOT NULL,used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS telegram_bindings(chat TEXT PRIMARY KEY,user TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS telegram_demos(user TEXT PRIMARY KEY,created INTEGER NOT NULL);
            ''')
            yield con
    finally:con.close()

def throttle(path,ip):
    now=int(time.time())
    with store(path) as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('DELETE FROM auth_attempts WHERE created<?',(now-3600,))
        count=con.execute('SELECT COUNT(*) FROM auth_attempts WHERE ip=? AND created>?',(ip,now-60)).fetchone()[0]
        if count>=8:raise ValueError('Слишком много попыток. Подождите минуту.')
        con.execute('INSERT INTO auth_attempts VALUES(?,?)',(ip,now))

def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)

def new_session(path,user):
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    with store(path) as con:
        con.execute('DELETE FROM web_sessions WHERE expires<?',(int(time.time()),))
        con.execute('INSERT INTO web_sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user,csrf,int(time.time())+86400*7))
    return dict(token=token,csrf=csrf,user=user)

def login(path,username,password,register=False):
    if not isinstance(username,str) or not isinstance(password,str):raise ValueError('Введите логин и пароль.')
    username=username.strip().lower()
    if not re.fullmatch(r'[a-z0-9_.-]{3,48}',username) or not 12<=len(password)<=128:
        raise ValueError('Логин: 3–48 латинских букв, цифр, точек или дефисов. Пароль: 12–128 символов.')
    if register:
        salt=secrets.token_bytes(16);user='web:'+secrets.token_hex(16)
        hashed=password_hash(password,salt)
        try:
            with store(path) as con:
                con.execute('INSERT INTO web_accounts VALUES(?,?,?,?,?)',(user,username,salt,hashed,int(time.time())))
        except sqlite3.IntegrityError:raise ValueError('Этот логин уже занят. Войдите или выберите другой.') from None
    else:
        with store(path) as con:row=con.execute('SELECT * FROM web_accounts WHERE login=?',(username,)).fetchone()
        salt=row['salt'] if row else b'fixed-dummy-salt!'
        hashed=password_hash(password,salt)
        if not row or not hmac.compare_digest(hashed,row['password']):raise ValueError('Логин или пароль неверны.')
        user=row['id']
    return new_session(path,user)

def session(path,token):
    if not isinstance(token,str) or len(token)>200:return None
    with store(path) as con:
        row=con.execute('SELECT user,csrf FROM web_sessions WHERE hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
    return dict(row) if row else None

def logout(path,token):
    with store(path) as con:con.execute('DELETE FROM web_sessions WHERE hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

def database_path(root,user,owner):
    if not user or user==owner:return root
    folder=root.parent/(root.stem+'_accounts');folder.mkdir(exist_ok=True)
    return folder/(hashlib.sha256(user.encode()).hexdigest()+'.sqlite3')

def link_token(path,user):
    token=secrets.token_urlsafe(24)
    with store(path) as con:
        con.execute('DELETE FROM telegram_links WHERE user=?',(user,))
        con.execute('INSERT INTO telegram_links VALUES(?,?,?,0)',(hashlib.sha256(token.encode()).hexdigest(),user,int(time.time())+600))
    return token

def bind_chat(path,token,chat,owner_chat):
    if chat==owner_chat:raise ValueError('Чат владельца уже используется администратором.')
    with store(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM telegram_links WHERE hash=? AND expires>? AND used=0',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        if not row:raise ValueError('Ссылка недействительна. Создайте новую в личном кабинете.')
        old=con.execute('SELECT user FROM telegram_bindings WHERE chat=?',(chat,)).fetchone()
        if old and old['user']!=row['user']:raise ValueError('Этот Telegram уже привязан к другому кабинету.')
        con.execute('DELETE FROM telegram_bindings WHERE user=?',(row['user'],))
        con.execute('INSERT OR REPLACE INTO telegram_bindings VALUES(?,?)',(chat,row['user']))
        con.execute('UPDATE telegram_links SET used=1 WHERE hash=?',(row['hash'],))
        return row['user']

def user_for_chat(path,chat):
    with store(path) as con:row=con.execute('SELECT user FROM telegram_bindings WHERE chat=?',(chat,)).fetchone()
    return row['user'] if row else None

def register_demo(path,chat):
    if not str(chat).isdigit() or int(chat)<=0:raise ValueError('Нужен личный чат Telegram.')
    user='tg:'+str(chat)
    with store(path) as con:con.execute('INSERT OR IGNORE INTO telegram_demos VALUES(?,?)',(user,int(time.time())))
    return user

def has_demo(path,user):
    with store(path) as con:return con.execute('SELECT 1 FROM telegram_demos WHERE user=?',(user,)).fetchone() is not None
