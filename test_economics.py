import unittest
from economics import calculate,validate_economics,normalized_dimensions
from catalog import enrich

class EconomicsTests(unittest.TestCase):
    def costs(self):return dict(price=1000,cost=400,commission_pct=20,tax_pct=6,acquiring_pct=1,logistics=80,storage=10,returns=20,other=30)
    def test_complete_profit_and_breakeven(self):
        c=calculate(self.costs())
        self.assertEqual(c['profit'],190)
        self.assertEqual(c['margin_pct'],19)
        self.assertAlmostEqual(c['breakeven'],739.73)
    def test_missing_is_not_free(self):
        c=self.costs();c['returns']=None
        self.assertIsNone(calculate(c)['profit'])
        self.assertIn('returns',calculate(c)['missing'])
    def test_manual_price_overrides_api_and_loss_is_explicit(self):
        c=self.costs();c['price']=100
        self.assertLess(calculate(c,1000)['profit'],0)
        c['price']=None
        self.assertEqual(calculate(c,1000)['profit'],190)
    def test_bad_values_and_zero_divisions(self):
        for v in (-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):validate_economics({'cost':v})
        c=self.costs();c.update(commission_pct=94,tax_pct=6,acquiring_pct=0,cost=0,price=0)
        result=calculate(c)
        self.assertIsNone(result['roi_pct']);self.assertIsNone(result['breakeven']);self.assertIsNone(result['margin_pct'])
    def test_dimensions_units_and_missing_values(self):
        self.assertEqual(normalized_dimensions(100,200,300,'mm')['volume_l'],6)
        self.assertIsNone(normalized_dimensions(0,2,3))
    def test_wb_size_prices_do_not_invent_a_single_price(self):
        rows=[dict(id='wb:42',offer='42',name='Item')]
        data=[{'data':{'listGoods':[{'nmID':42,'sizes':[{'discountedPrice':100},{'discountedPrice':200}]}]}},
              {'cards':[{'nmID':42,'vendorCode':'SELLER-42','title':'Название','dimensions':{'height':10,'width':20,'length':30}}]}]
        warnings=enrich(rows,'WB',lambda *args:data.pop(0),{})
        self.assertFalse(warnings);self.assertIsNone(rows[0]['price'])
        self.assertEqual(rows[0]['price_max'],200);self.assertEqual(rows[0]['offer'],'SELLER-42')
    def test_optional_catalog_failure_preserves_stock(self):
        rows=[dict(id='wb:42',offer='42',name='Item',quantity=7)]
        def fail(*args):raise RuntimeError('denied')
        self.assertEqual(len(enrich(rows,'WB',fail,{})),2)
        self.assertEqual(rows[0]['quantity'],7)

class ProfitAlertTests(unittest.TestCase):
    def test_loss_alert_is_not_repeated_and_recovery_cancels_pending(self):
        import app,json,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            old=app.DB;app.DB=Path(folder)/'test.sqlite3'
            try:
                app.init()
                p=dict(id='wb:42',platform='WB',offer='A',name='A',quantity=100,reserved=0,daily=1,price=100)
                costs=dict(price=None,cost=150,commission_pct=0,tax_pct=0,acquiring_pct=0,logistics=0,storage=0,returns=0,other=0)
                with app.connect() as db:db.execute('INSERT INTO economics VALUES(?,?)',(p['id'],json.dumps(costs)))
                app.save_snapshot([p],'live');app.save_snapshot([p],'live')
                alerts=app.state()['alerts']
                self.assertEqual(len(alerts),1);self.assertEqual(alerts[0]['category'],'profit')
                self.assertEqual(alerts[0]['status'],'pending')
                app.save_snapshot([{**p,'price':200}],'live')
                self.assertEqual(app.state()['alerts'][0]['status'],'obsolete')
            finally:app.DB=old
