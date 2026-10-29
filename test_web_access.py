import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import app,billing

class WebAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=app.DB;app.DB=Path(self.temp.name)/'root.sqlite3'
        self.env=patch.dict(os.environ,{'TELEGRAM_CHAT_ID':'42','TELEGRAM_BOT_TOKEN':'test-token','OZON_API_KEY':'OWNER-PRIVATE-KEY','ZAPAS_SBP_ENABLED':'0'})
        self.env.start();app.init();app.save_snapshot(app.demo_products(),'demo')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.MiniAppHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.env.stop();app.DB=self.old;self.temp.cleanup()
    def client(self):return urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,client,path,data=None,csrf=None,origin=None):
        headers={'Content-Type':'application/json','Origin':origin or self.url}
        if csrf:headers['X-CSRF-Token']=csrf
        with client.open(urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)) as response:
            return json.load(response)
    def signup(self,name):
        client=self.client();self.request(client,'/api/auth/register',{'login':name,'password':'test-pilot-only-password'})
        return client,self.request(client,'/api/state')
    def test_public_website_has_no_shop_data_and_requires_session(self):
        client=self.client()
        with client.open(self.url+'/') as response:html=response.read().decode()
        self.assertIn('auth-form',html);self.assertNotIn('OWNER-PRIVATE',html)
        with self.assertRaises(urllib.error.HTTPError) as error:self.request(client,'/api/state')
        self.assertEqual(error.exception.code,401);error.exception.close()
    def test_two_real_http_sessions_keep_settings_and_secrets_separate(self):
        a,sa=self.signup('seller-a');b,sb=self.signup('seller-b')
        self.assertFalse(sa['connections']['ozon']);self.assertFalse(sb['connections']['ozon'])
        self.request(a,'/api/marketplace',{'marketplace':'Ozon'},sa['csrf'])
        self.request(b,'/api/marketplace',{'marketplace':'WB'},sb['csrf'])
        self.assertTrue(all(p['platform']=='Ozon' for p in self.request(a,'/api/state')['products']))
        self.assertTrue(all(p['platform']=='WB' for p in self.request(b,'/api/state')['products']))
        self.request(a,'/api/settings',{'threshold':3,'days':2},sa['csrf'])
        self.assertEqual(self.request(b,'/api/state')['settings']['threshold'],10)
        self.assertNotIn('OWNER-PRIVATE',json.dumps(self.request(a,'/api/state')))
    def test_expired_trial_is_enforced_on_server_and_cannot_restart(self):
        client,state=self.signup('expired')
        with app.accounts.store(app.DB) as db:user=db.execute("SELECT id FROM web_accounts WHERE login='expired'").fetchone()[0]
        billing.start_trial(app.DB,user,clock=time.time()-8*86400)
        billing.start_trial(app.DB,user)
        for path,data in [('/api/state',None),('/site-export.zip',None),('/api/settings',{'threshold':1,'days':1})]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.request(client,path,data,state['csrf'])
            self.assertEqual(error.exception.code,402);error.exception.close()
        status=self.request(client,'/api/access')
        self.assertFalse(status['subscription']['allowed'])
        self.assertIn('csrf',status)
    def test_wrong_origin_and_shared_bot_configuration_are_rejected(self):
        client=self.client()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(client,'/api/auth/register',{'login':'evil','password':'test-pilot-only-password'},origin='https://evil.example')
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_public_host_is_service_owned_inside_customer_scope(self):
        with app.connect() as db:
            db.execute("INSERT OR REPLACE INTO workspace VALUES('bot',?)",(json.dumps({'app_url':'https://shop.example/miniapp'}),))
        handler=object.__new__(app.MiniAppHandler)
        handler.headers={'Host':'shop.example'};handler.server=self.server
        with app.account_scope('web:isolated'):
            self.assertTrue(handler.valid_host())
        client,state=self.signup('seller-c')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(client,'/api/telegram-menu',{},state['csrf'])
        self.assertEqual(error.exception.code,403);error.exception.close()
