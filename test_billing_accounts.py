import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import accounts,billing,app

class AccountBillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'root.sqlite3'
    def tearDown(self):self.tmp.cleanup()
    def test_password_session_and_logout(self):
        session=accounts.login(self.root,'tester','unique-test-password',True)
        self.assertEqual(accounts.session(self.root,session['token'])['user'],session['user'])
        with self.assertRaises(ValueError):accounts.login(self.root,'tester','wrong-test-password')
        with accounts.store(self.root) as db:
            row=db.execute('SELECT * FROM web_accounts').fetchone()
            self.assertNotIn('unique-test-password',str(dict(row)))
            self.assertNotIn(session['token'],str(dict(db.execute('SELECT * FROM web_sessions').fetchone())))
        accounts.logout(self.root,session['token']);self.assertIsNone(accounts.session(self.root,session['token']))
    def test_accounts_do_not_share_credentials_or_products(self):
        old=app.DB;app.DB=self.root
        try:
            with patch.dict(os.environ,OZON_API_KEY='OWNER-PRIVATE',TELEGRAM_CHAT_ID='1'):
                app.init()
                for user,name in [('web:a','A'),('web:b','B')]:
                    with app.account_scope(user):
                        app.save_snapshot([dict(id='same',platform='Ozon',offer=name,name=name,quantity=5,reserved=0,daily=1)],'live')
                        self.assertIsNone(app.credential('OZON_API_KEY'))
                with app.account_scope('web:a'):self.assertEqual(app.state()['products'][0]['offer'],'A')
                with app.account_scope('web:b'):self.assertEqual(app.state()['products'][0]['offer'],'B')
        finally:app.DB=old
    def test_trial_starts_once_and_boundary_is_enforced(self):
        self.assertEqual(billing.access(self.root,'a',clock=100)['status'],'pending')
        billing.start_trial(self.root,'a',clock=100);billing.start_trial(self.root,'a',clock=1000)
        self.assertEqual(billing.access(self.root,'a',clock=1000)['trial_started'],100)
        self.assertTrue(billing.access(self.root,'a',clock=100+7*86400-1)['allowed'])
        self.assertFalse(billing.access(self.root,'a',clock=100+7*86400)['allowed'])
    def payment(self):
        with billing.registry(self.root) as db:
            db.execute("INSERT INTO purchases VALUES('order','web:a',299,100,'remote','pending',0,'https://example.com')")
        return dict(id='remote',paid=True,status='succeeded',amount={'value':'299.00','currency':'RUB'},metadata={'order_id':'order','user':'web:a'})
    def test_duplicate_webhooks_do_not_extend_twice(self):
        remote=self.payment()
        self.assertTrue(billing.apply_payment(self.root,remote,clock=100))
        self.assertFalse(billing.apply_payment(self.root,remote,clock=200))
        self.assertEqual(billing.access(self.root,'web:a',clock=200)['paid_until'],100+30*86400)
    def test_pending_wrong_amount_wrong_user_cannot_grant_access(self):
        remote=self.payment();remote['status']='pending'
        self.assertFalse(billing.apply_payment(self.root,remote))
        remote['status']='succeeded';remote['amount']['value']='1.00'
        with self.assertRaises(ValueError):billing.apply_payment(self.root,remote)
        remote['amount']['value']='299.00';remote['metadata']['user']='web:b'
        with self.assertRaises(ValueError):billing.apply_payment(self.root,remote)
        self.assertIsNone(billing.access(self.root,'web:a')['paid_until'])
    def test_checkout_disabled_until_explicit_setup_and_web_only(self):
        with patch.dict(os.environ,{'ZAPAS_SBP_ENABLED':'0'}):
            with self.assertRaises(ValueError):billing.create_order(self.root,'web:a',None,'https://example.com')
    def test_sbp_order_is_idempotent_and_does_not_grant_access(self):
        config={'ZAPAS_SBP_ENABLED':'1','ZAPAS_PRICE_APPROVED':'1','YOOKASSA_SHOP_ID':'test','YOOKASSA_SECRET_KEY':'test','ZAPAS_WEB_ORIGIN':'https://example.com','ZAPAS_TERMS_URL':'https://example.com/terms','ZAPAS_SUPPORT_URL':'https://example.com/support'}
        calls=[]
        def request(method,path,body,key):
            calls.append((method,path,body,key));return dict(id='remote-42',status='pending',confirmation={'confirmation_url':'https://example.com/payment'})
        with patch.dict(os.environ,config):
            first=billing.create_order(self.root,'web:a',request,'https://example.com/web-app',True)
            second=billing.create_order(self.root,'web:a',request,'https://example.com/web-app',True)
            with self.assertRaises(ValueError):billing.create_order(self.root,'tg:42',request,'https://example.com/web-app',True)
            with self.assertRaises(ValueError):billing.create_order(self.root,'web:b',request,'https://example.com/web-app',False)
        self.assertEqual(first,second);self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2]['payment_method_data']['type'],'sbp')
        self.assertIsNone(billing.access(self.root,'web:a')['paid_until'])
    def test_link_is_one_use_and_chat_cannot_be_stolen(self):
        token=accounts.link_token(self.root,'web:a')
        self.assertEqual(accounts.bind_chat(self.root,token,'42','1'),'web:a')
        with self.assertRaises(ValueError):accounts.bind_chat(self.root,token,'43','1')
        other=accounts.link_token(self.root,'web:b')
        with self.assertRaises(ValueError):accounts.bind_chat(self.root,other,'42','1')
        self.assertEqual(accounts.user_for_chat(self.root,'42'),'web:a')
