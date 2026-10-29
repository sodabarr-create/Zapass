import copy
import io
import os
from pathlib import Path
import unittest
import zipfile
from studio import SITE,BOT,validate_site,validate_bot,render_site,site_archive,bot_reply
from vault import crypt


class StudioTests(unittest.TestCase):
    def test_site_defaults_not_mutated(self):
        config=validate_site({})
        config['features'].append('extra')
        self.assertEqual(len(SITE['features']),3)

    def test_site_rejects_executable_urls(self):
        for url in ['javascript:alert(1)','http://example.com','https://user:password@example.com']:
            with self.assertRaises(ValueError):
                validate_site({'url':url})

    def test_site_text_is_escaped(self):
        html=render_site({**SITE,'headline':'<script>alert(1)</script>'})
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_site_section_toggles(self):
        html=render_site({**SITE,'show_features':False,'show_steps':False})
        self.assertNotIn('<section class="features">',html)
        self.assertNotIn('<section class="steps">',html)

    def test_missing_contact_does_not_create_fake_signup(self):
        html=render_site(SITE)
        self.assertIn('Контакт для записи пока не указан',html)
        self.assertNotIn('class="cta" href=',html)

    def test_export_is_standalone_and_contains_no_database(self):
        archive=site_archive(SITE,Path(__file__).resolve().parents[1])
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.assertEqual(set(z.namelist()),{'index.html','site.css','manrope.ttf','Manrope-OFL.txt','README.txt'})
            html=z.read('index.html').decode()
            self.assertIn('href="site.css"',html)
            self.assertNotIn('страница не опубликована',html)

    def test_duplicate_bot_buttons_rejected(self):
        with self.assertRaises(ValueError):
            validate_bot({**BOT,'summary_label':'Одно','stock_label':'Одно'})

    def test_preview_uses_real_menu_logic_and_marks_demo(self):
        snapshot={'meta':{'mode':'demo'},'products':[{'platform':'Ozon','offer':'A','quantity':4,'level':'low'}]}
        reply=bot_reply(BOT['summary_label'],BOT,snapshot)
        self.assertIn('ДЕМО',reply)
        self.assertIn('Требуют внимания: 1',reply)
        self.assertIn('A: 4 шт.',bot_reply(BOT['stock_label'],BOT,snapshot))

    def test_custom_greeting(self):
        self.assertEqual(bot_reply('/start',{**BOT,'greeting':'Здравствуйте, Анна!'},{}),'Здравствуйте, Анна!')

    def test_all_published_commands_have_a_real_handler(self):
        from bot_content import COMMANDS
        snapshot={'meta':{'mode':'demo'},'products':[],'alerts':[],'connections':{}}
        for item in COMMANDS:
            for suffix in ('','@ZapasPilotBot','@ZapasPilotBot payload'):
                with self.subTest(command=item['command'],suffix=suffix):
                    answer=bot_reply('/'+item['command']+suffix,BOT,snapshot)
                    self.assertTrue(answer)
                    self.assertNotIn('Такой команды пока нет',answer)
                    self.assertLessEqual(len(answer),4000)

    def test_every_keyboard_text_has_a_handler(self):
        from telegram_app import bot_keyboard
        snapshot={'meta':{'mode':'demo'},'products':[],'alerts':[],'connections':{}}
        config={**BOT,'app_url':'https://example.com/miniapp'}
        for row in bot_keyboard(config)['keyboard']:
            for button in row:
                if 'web_app' in button:continue
                answer=bot_reply(button['text'],config,snapshot)
                self.assertNotIn('Такой команды пока нет',answer)

    def test_help_explains_setup_and_unknown_command_is_actionable(self):
        self.assertIn('Client ID',bot_reply('/help',BOT,{}))
        self.assertIn('/help',bot_reply('/unknown',BOT,{}))

    @unittest.skipUnless(os.name=='nt','DPAPI available on Windows only')
    def test_windows_credentials_encrypt_and_roundtrip(self):
        plain=b'test-only-not-a-real-token'
        encrypted=crypt(plain)
        self.assertNotIn(plain,encrypted)
        self.assertEqual(crypt(encrypted,True),plain)


if __name__=='__main__':
    unittest.main()
