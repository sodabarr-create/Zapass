import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from cryptography.fernet import Fernet
from vault import crypt
from deployment import validate_environment, configure_public_app
import sqlite3
from contextlib import closing
import json

class DeploymentTests(unittest.TestCase):
    def test_portable_encryption_and_wrong_key(self):
        with patch.dict(os.environ,{'ZAPAS_MASTER_KEY':Fernet.generate_key().decode()}):
            encrypted=crypt(b'test-secret')
            self.assertNotIn(b'test-secret',encrypted)
            self.assertEqual(crypt(encrypted,True),b'test-secret')
        with patch.dict(os.environ,{'ZAPAS_MASTER_KEY':Fernet.generate_key().decode()}):
            with self.assertRaises(ValueError):crypt(encrypted,True)
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):crypt(encrypted,True)

    def test_production_rejects_missing_and_unsafe_config(self):
        env={'ZAPAS_ENV':'production','ZAPAS_MASTER_KEY':Fernet.generate_key().decode(),'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'123','ZAPAS_WEB_ORIGIN':'https://example.com'}
        with patch.dict(os.environ,env,clear=True):validate_environment()
        for bad in ['http://example.com','https://user:pass@example.com','https://example.com/path','https://example.com?key=x']:
            with patch.dict(os.environ,{**env,'ZAPAS_WEB_ORIGIN':bad},clear=True):
                with self.assertRaises(ValueError):validate_environment()
        with patch.dict(os.environ,{'ZAPAS_ENV':'production'},clear=True):
            with self.assertRaises(ValueError):validate_environment()

    def test_stable_url_replaces_old_tunnel_preserving_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            database=Path(folder)/'test.sqlite3'
            with closing(sqlite3.connect(database)) as con, con:
                con.execute('CREATE TABLE workspace(key TEXT PRIMARY KEY,payload TEXT)')
                con.execute('INSERT INTO workspace VALUES(?,?)',('bot',json.dumps({'app_url':'https://old.example/miniapp','name':'My bot'})))
            with patch.dict(os.environ,{'ZAPAS_WEB_ORIGIN':'https://new.example','ZAPAS_BOT_ENABLED':'1'}):configure_public_app(database)
            with closing(sqlite3.connect(database)) as con, con:
                config=json.loads(con.execute('SELECT payload FROM workspace').fetchone()[0])
            self.assertEqual(config['app_url'],'https://new.example/miniapp')
            self.assertEqual(config['name'],'My bot')
            self.assertTrue(config['enabled'])
