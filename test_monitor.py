import os
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = app.DB
        app.DB = Path(self.temp.name) / 'test.sqlite3'
        app.init()

    def tearDown(self):
        app.DB = self.previous
        self.temp.cleanup()

    def product(self, quantity=5, daily=2):
        return dict(id='ozon:1:fbo', platform='Ozon', offer='A', name='A', quantity=quantity, reserved=0, daily=daily)

    def test_risk_boundaries_and_unknown_forecast(self):
        self.assertEqual(app.risk(self.product(0), 10, 7)[0], 'empty')
        self.assertEqual(app.risk(self.product(10), 10, 7)[0], 'low')
        self.assertEqual(app.risk(self.product(14), 10, 7)[0], 'soon')
        self.assertEqual(app.risk(self.product(15), 10, 7)[0], 'ok')
        self.assertEqual(app.risk(self.product(15, None), 10, 7), ('ok', None))
        self.assertEqual(app.risk(self.product(15, 0), 10, 7), ('ok', None))

    def test_same_risk_does_not_repeat(self):
        app.save_snapshot([self.product()], 'live')
        app.save_snapshot([self.product(4)], 'live')
        self.assertEqual(len(app.state()['alerts']), 1)

    def test_recovery_rearms_and_obsoletes_old_pending(self):
        app.save_snapshot([self.product()], 'live')
        app.save_snapshot([self.product(100)], 'live')
        self.assertEqual(app.state()['alerts'][0]['status'], 'obsolete')
        app.save_snapshot([self.product()], 'live')
        self.assertEqual(len(app.state()['alerts']), 2)
        self.assertEqual(app.state()['alerts'][0]['status'], 'pending')

    def test_demo_never_enters_delivery_queue(self):
        app.save_snapshot(app.demo_products(), 'demo')
        self.assertTrue(all(a['status'] == 'demo' for a in app.state()['alerts']))

    def test_marketplace_selection_filters_products_and_events(self):
        app.save_snapshot(app.demo_products(), 'demo')
        for marketplace in ('Ozon','WB'):
            with app.connect() as con:
                con.execute("INSERT OR REPLACE INTO meta VALUES('marketplace',?)",(marketplace,))
            snapshot=app.state()
            self.assertTrue(snapshot['products'])
            self.assertTrue(all(p['platform']==marketplace for p in snapshot['products']))
            ids={p['id'] for p in snapshot['products']}
            self.assertTrue(all(a['product'] in ids or (marketplace=='WB' and a['product']=='tariff') for a in snapshot['alerts']))
            if marketplace=='Ozon':self.assertIsNone(snapshot['tariffs'])

    def test_delivery_skips_other_marketplace_without_consuming_attempts(self):
        products=[self.product(),dict(self.product(),id='wb:2',platform='WB',offer='WB-ONLY')]
        app.save_snapshot(products,'live')
        with app.connect() as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES('marketplace','Ozon')")
        with patch.dict(os.environ,TELEGRAM_BOT_TOKEN='test',TELEGRAM_CHAT_ID='1'):
            with patch.object(app,'request_json',return_value={'ok':True}) as send:
                app.deliver()
        self.assertEqual(send.call_count,1)
        with app.connect() as con:
            wb=con.execute("SELECT status,attempts FROM alerts WHERE product='wb:2'").fetchone()
        self.assertEqual(tuple(wb),('pending',0))

    def test_removed_product_obsoletes_pending(self):
        app.save_snapshot([self.product()], 'live')
        app.save_snapshot([], 'live')
        self.assertEqual(app.state()['alerts'][0]['status'], 'obsolete')

    def test_failed_telegram_remains_pending(self):
        app.save_snapshot([self.product()], 'live')
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_CHAT_ID='1'):
            with patch.object(app, 'request_json', side_effect=RuntimeError('offline')):
                app.deliver()
        alert = app.state()['alerts'][0]
        self.assertEqual(alert['status'], 'pending')
        self.assertEqual(alert['attempts'], 1)

    def test_confirmed_telegram_is_sent_once(self):
        app.save_snapshot([self.product()], 'live')
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_CHAT_ID='1'):
            with patch.object(app, 'request_json', return_value={'ok': True}) as req:
                app.deliver()
                app.deliver()
        self.assertEqual(req.call_count, 1)
        self.assertEqual(app.state()['alerts'][0]['status'], 'sent')

    def test_ozon_dict_stocks_and_analytics(self):
        responses = [{'result': {'items': [{'product_id': 1, 'offer_id': 'A', 'stocks':
            {'fbo': {'type': 'fbo', 'present': 12, 'reserved': 2, 'sku': 42}}}]}},
            {'result': {'data': [{'dimensions': [{'id': '42'}, {'id': str(dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=1))}], 'metrics': [28]}]}}]
        with patch.dict(os.environ, OZON_CLIENT_ID='test', OZON_API_KEY='test'):
            with patch.object(app, 'request_json', side_effect=responses):
                products, warning = app.ozon_products()
        self.assertEqual(products[0]['quantity'], 10)
        self.assertEqual(products[0]['daily'], 2)
        self.assertEqual(warning, '')

    def test_analytics_failure_does_not_invent_demand(self):
        responses = [{'result': {'items': [{'product_id': 1, 'offer_id': 'A', 'stocks':
            [{'type': 'fbo', 'present': 12, 'sku': 42}]}]}}, RuntimeError('denied')]
        with patch.dict(os.environ, OZON_CLIENT_ID='test', OZON_API_KEY='test'):
            with patch.object(app, 'request_json', side_effect=responses):
                products, warning = app.ozon_products()
        self.assertIsNone(products[0]['daily'])
        self.assertTrue(warning)

    def test_snapshot_is_atomic(self):
        app.save_snapshot([self.product()], 'live')
        with self.assertRaises(KeyError):
            app.save_snapshot([{'id': 'broken'}], 'live')
        self.assertEqual(app.state()['products'][0]['offer'], 'A')

    def test_catalog_fields_survive_snapshot_storage(self):
        product={**self.product(),'price':1000,'dimensions':{'height_cm':10,'width_cm':20,'length_cm':30,'volume_l':6},'price_source':'API'}
        app.save_snapshot([product],'live')
        saved=app.state()['products'][0]
        self.assertEqual(saved['price'],1000)
        self.assertEqual(saved['dimensions']['volume_l'],6)

    def test_wb_sums_sizes_and_ignores_cancelled_orders(self):
        day=str(dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=1))
        responses=[{'data':{'items':[{'nmId':42,'quantity':3},{'nmId':42,'quantity':5}]}},
                   [{'srid':'one','date':day,'nmId':42,'isCancel':False},
                    {'srid':'one','date':day,'nmId':42,'isCancel':False},
                    {'srid':'two','date':day,'nmId':42,'isCancel':True}]]
        with patch.dict(os.environ,WB_API_TOKEN='test'):
            with patch.object(app,'request_json',side_effect=responses):
                products,warning=app.wb_products()
        self.assertEqual(products[0]['quantity'],8)
        self.assertAlmostEqual(products[0]['daily'],1/14)
        self.assertEqual(len(products[0]['history']),56)
        self.assertEqual(warning,'')

    def test_tariff_failure_preserves_snapshot_but_marks_error(self):
        app.refresh_tariffs(demo=True)
        with app.connect() as con:
            con.execute("UPDATE tariffs SET source='wb_api'")
        with patch.dict(os.environ,WB_API_TOKEN='test'):
            with patch.object(app,'request_json',side_effect=RuntimeError('disabled')):
                app.refresh_tariffs()
        with app.connect() as con:
            row=dict(con.execute('SELECT * FROM tariffs').fetchone())
        self.assertTrue(row['error'])
        self.assertTrue(row['payload'])

    def test_stale_or_wrong_date_tariff_is_not_used(self):
        import json
        from planner import DEFAULTS
        product=self.product()
        product['platform']='WB'
        profile={**DEFAULTS,'warehouse_id':507,'coefficient_now':2}
        tariffs={'source':'wb_api','error':'','updated':app.now(),'payload':json.dumps([
            {'warehouseID':507,'boxTypeID':2,'date':'2000-01-01','coefficient':0,'allowUnload':True}])}
        plan=app.enriched_plan(product,profile,tariffs)
        self.assertEqual(plan['tariff_source'],'manual')
        self.assertEqual(plan['profile']['coefficient_now'],2)

    def test_matching_fresh_tariff_is_used(self):
        import json
        from planner import DEFAULTS
        product=self.product()
        product['platform']='WB'
        profile={**DEFAULTS,'warehouse_id':507,'coefficient_now':2}
        day=str(dt.datetime.now(dt.timezone.utc).date()+dt.timedelta(days=5))
        tariffs={'source':'wb_api','error':'','updated':app.now(),'payload':json.dumps([
            {'warehouseID':507,'boxTypeID':2,'date':day,'coefficient':0,'allowUnload':True}])}
        plan=app.enriched_plan(product,profile,tariffs)
        self.assertEqual(plan['tariff_source'],'wb_api')
        self.assertEqual(plan['profile']['coefficient_now'],0)

    def test_demo_tariff_never_used_for_live_cost(self):
        import json
        from planner import DEFAULTS
        product={**self.product(),'platform':'WB'}
        day=str(dt.datetime.now(dt.timezone.utc).date()+dt.timedelta(days=5))
        tariffs={'source':'demo','error':'','updated':app.now(),'payload':json.dumps([
            {'warehouseID':507,'boxTypeID':2,'date':day,'coefficient':0,'allowUnload':True}])}
        plan=app.enriched_plan(product,{**DEFAULTS,'warehouse_id':507,'coefficient_now':2},tariffs)
        self.assertEqual(plan['tariff_source'],'manual')

    def test_live_profiles_are_not_silently_seeded(self):
        app.seed_demo_profiles()
        app.save_snapshot([self.product()], 'live')
        self.assertIsNone(app.state()['products'][0]['profile'])

    def test_settings_survive_snapshot_refresh(self):
        import json
        from planner import DEFAULTS
        with app.connect() as con:
            con.execute('INSERT INTO profiles VALUES(?,?)',(self.product()['id'],json.dumps({**DEFAULTS,'transit':11})))
        app.save_snapshot([self.product()], 'live')
        app.save_snapshot([self.product()], 'live')
        self.assertEqual(app.state()['products'][0]['profile']['transit'],11)


if __name__ == '__main__':
    unittest.main()
