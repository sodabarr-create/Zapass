import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from urllib.parse import urlencode
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import app
from studio import BOT,validate_bot
from telegram_app import verify_init_data,validate_app_url,bot_keyboard

TOKEN='test-only-not-a-real-bot-token'


def signed(user=42,age=0,token=TOKEN):
    data={'auth_date':str(int(time.time())-age),'query_id':'test-query','user':json.dumps({'id':user,'first_name':'Тест'})}
    check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    data['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(data)


class SignatureTests(unittest.TestCase):
    def test_valid_owner(self):
        self.assertEqual(verify_init_data(signed(),TOKEN,'42'),42)

    def test_other_user_rejected(self):
        with self.assertRaises(ValueError):verify_init_data(signed(user=43),TOKEN,'42')

    def test_expired_and_future_auth_rejected(self):
        for age in (3605,-100):
            with self.assertRaises(ValueError):verify_init_data(signed(age=age),TOKEN,'42')

    def test_modified_signature_rejected(self):
        with self.assertRaises(ValueError):verify_init_data(signed()+'0',TOKEN,'42')

    def test_missing_token_rejected(self):
        with self.assertRaises(ValueError):verify_init_data(signed(),'','42')

    def test_repeated_fields_rejected(self):
        with self.assertRaises(ValueError):verify_init_data(signed()+'&auth_date=0',TOKEN,'42')

    def test_wrong_bot_token_rejected(self):
        with self.assertRaises(ValueError):verify_init_data(signed(),TOKEN+'x','42')

    def test_https_url_required(self):
        for url in ('http://example.com/miniapp','https://localhost/miniapp','https://example.com','https://example.com/miniapp?token=x'):
            with self.assertRaises(ValueError):validate_app_url(url)
        self.assertEqual(validate_app_url('https://example.com/miniapp'),'https://example.com/miniapp')

    def test_web_app_button_uses_saved_url(self):
        config=validate_bot({**BOT,'app_url':'https://example.com/miniapp'})
        self.assertEqual(bot_keyboard(config)['keyboard'][0][0]['web_app']['url'],config['app_url'])
        self.assertNotIn('web_app',bot_keyboard(BOT)['keyboard'][0][0])


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.previous=app.DB
        app.DB=Path(self.temp.name)/'gateway.sqlite3'
        app.init()
        app.save_snapshot(app.demo_products(),'demo')
        self.environment=patch.dict(os.environ,TELEGRAM_BOT_TOKEN=TOKEN,TELEGRAM_CHAT_ID='42')
        self.environment.start()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.MiniAppHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.environment.stop();app.DB=self.previous;self.temp.cleanup()

    def request(self,path,auth=None,body=None):
        headers={'Content-Type':'application/json','X-CSRF-Token':app.CSRF}
        if auth:headers['X-Telegram-Init-Data']=auth
        return urllib.request.urlopen(urllib.request.Request(self.url+path,
            data=None if body is None else json.dumps(body).encode(),headers=headers))

    def test_unsigned_request_never_returns_seller_data(self):
        for path in ('/api/state','/site-export.zip','/site-preview'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request(path)
            self.assertEqual(error.exception.code,401)
            error.exception.close()
        with self.request('/') as response:
            html=response.read().decode()
        self.assertIn('auth-form',html)
        self.assertNotIn('HOME-021',html)

    def test_signed_owner_can_read_state(self):
        with self.request('/api/state',signed()) as response:
            data=json.load(response)
        self.assertEqual(len(data['products']),5)
        self.assertNotIn(TOKEN,json.dumps(data))

    def test_other_account_cannot_write_rules(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/settings',signed(user=43),{'threshold':3,'days':7})
        self.assertEqual(error.exception.code,401)
        error.exception.close()
        self.assertEqual(app.state()['settings']['threshold'],10)

    def test_owner_can_write_rules(self):
        with self.request('/api/settings',signed(),{'threshold':3,'days':7}) as response:
            self.assertEqual(response.status,200)
        self.assertEqual(app.state()['settings']['threshold'],3)

    def test_marketplace_choice_is_validated_and_owner_only(self):
        for auth,value,code in ((signed(user=43),'WB',401),(signed(),'unknown',400)):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/marketplace',auth,{'marketplace':value})
            self.assertEqual(error.exception.code,code)
            error.exception.close()
        with self.request('/api/marketplace',signed(),{'marketplace':'WB'}) as response:
            self.assertEqual(response.status,200)
        self.assertEqual(app.state()['marketplace'],'WB')
        self.assertTrue(all(p['platform']=='WB' for p in app.state()['products']))

    def test_public_shell_has_sdk_and_no_secret(self):
        with self.request('/miniapp') as response:
            html=response.read().decode()
            self.assertIn('https://telegram.org',response.headers['Content-Security-Policy'])
        self.assertIn('telegram-web-app.js',html)
        self.assertIn('miniapp.js',html)
        self.assertNotIn(TOKEN,html)

    def test_assets_are_compressed_versioned_and_cached(self):
        import gzip
        req=urllib.request.Request(self.url+'/app.js?v=test',headers={'Accept-Encoding':'gzip'})
        with urllib.request.urlopen(req) as response:
            self.assertEqual(response.headers['Content-Encoding'],'gzip')
            self.assertIn('immutable',response.headers['Cache-Control'])
            data=response.read();tag=response.headers['ETag']
        self.assertIn(b'function render',gzip.decompress(data))
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(self.url+'/app.js?v=test',headers={'If-None-Match':tag}))
        self.assertEqual(error.exception.code,304);error.exception.close()

    def test_keepalive_never_caches_private_data_after_asset(self):
        from http.client import HTTPConnection
        connection=HTTPConnection('127.0.0.1',self.server.server_port)
        try:
            connection.request('GET','/style.css');response=connection.getresponse();response.read()
            connection.request('GET','/api/state',headers={'X-Telegram-Init-Data':signed()})
            response=connection.getresponse();data=json.loads(response.read())
            self.assertEqual(response.getheader('Cache-Control'),'no-store')
            self.assertIsNone(response.getheader('ETag'))
            self.assertEqual(len(data['products']),5)
        finally:connection.close()

    def test_launch_shell_uses_local_sdk_and_visible_retry(self):
        with self.request('/miniapp') as response:html=response.read().decode()
        self.assertIn('/telegram-web-app.js?v=',html)
        self.assertNotIn('src="https://telegram.org',html)
        self.assertIn('startup-retry',html)

    def test_registered_telegram_demo_is_isolated_and_cannot_connect_keys(self):
        import accounts
        accounts.register_demo(app.DB,'43')
        with self.request('/api/state',signed(user=43)) as response:data=json.load(response)
        self.assertTrue(data['account']['demo'])
        self.assertFalse(data['account']['owner'])
        with self.request('/api/settings',signed(user=43),{'threshold':3,'days':2}) as response:
            self.assertEqual(response.status,200)
        self.assertEqual(app.state()['settings']['threshold'],10)
        with self.request('/api/state',signed(user=43)) as response:
            self.assertEqual(json.load(response)['settings']['threshold'],3)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/connections',signed(user=43),{'provider':'ozon','OZON_CLIENT_ID':'a','OZON_API_KEY':'b'})
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_auth_error_explains_expired_or_unregistered_entry(self):
        for signature,expected in ((signed(age=3605),'устарел'),(signed(user=43),'/start')):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/state',signature)
            data=json.load(error.exception);error.exception.close()
            self.assertIn(expected,data['error'])
            self.assertNotIn('Нет подтверждённого входа владельца',data['error'])


if __name__=='__main__':unittest.main()
