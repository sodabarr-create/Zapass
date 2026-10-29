import datetime as dt
import unittest
from unittest.mock import patch
import os
import app
from planner import forecast, validate_profile, DEFAULTS

TODAY = dt.date(2026,10,29)


class PlannerTests(unittest.TestCase):
    def product(self, quantity=100, daily=10):
        return dict(quantity=quantity,daily=daily)

    def profile(self, **kwargs):
        return {**DEFAULTS, 'preparation':2,'transit':3,'acceptance_min':2,
                'acceptance_max':10,'coefficient_now':2,'coefficient_wait':0, **kwargs}

    def test_lead_until_sale_not_only_shipping(self):
        result=forecast(self.product(),self.profile(),TODAY)
        self.assertEqual((result['lead_min'],result['lead_max']),(7,15))
        self.assertAlmostEqual(result['gap_min'],0)
        self.assertAlmostEqual(result['gap_max'],5)
        self.assertEqual(result['order_by'],'2026-10-22')

    def test_wait_adds_to_both_arrival_bounds(self):
        result=forecast(self.product(),self.profile(),TODAY)
        a,b=result['scenarios']
        self.assertEqual(a['arrival_min'],'2026-11-05')
        self.assertEqual(b['arrival_min'],'2026-11-08')
        self.assertEqual(b['gap_max'],8)

    def test_spike_is_separate_from_14_day_baseline(self):
        p=self.product()
        p['history']=[{'date':str(TODAY-dt.timedelta(days=n)),'units':30 if n<=3 else 10} for n in range(1,15)]
        result=forecast(p,self.profile(),TODAY)
        self.assertAlmostEqual(result['model']['baseline'],(90+110)/14)
        self.assertEqual(result['model']['recent'],30)
        self.assertTrue(result['model']['spike'])
        self.assertLess(result['fast_coverage'],result['coverage'])

    def test_missing_history_is_not_seasonality(self):
        result=forecast(self.product(),self.profile(),TODAY)
        self.assertEqual(result['model']['seasonal_status'],'insufficient')
        self.assertFalse(result['model']['spike'])

    def test_weekday_pattern_requires_56_complete_days(self):
        p=self.product()
        p['history']=[{'date':str(TODAY-dt.timedelta(days=n)),'units':20 if (TODAY-dt.timedelta(days=n)).weekday()==0 else 5} for n in range(1,57)]
        self.assertEqual(forecast(p,self.profile(),TODAY)['model']['seasonal_status'],'weekday')
        p['history'].pop()
        self.assertEqual(forecast(p,self.profile(),TODAY)['model']['seasonal_status'],'insufficient')

    def test_unknown_coefficient_does_not_mean_free(self):
        result=forecast(self.product(),self.profile(coefficient_now=None),TODAY)
        self.assertIsNone(result['scenarios'][0]['fee'])
        self.assertIsNone(result['scenarios'][0]['total_min'])
        self.assertIn('укажите',result['comparison'])

    def test_zero_coefficient_means_free_acceptance(self):
        result=forecast(self.product(),self.profile(coefficient_now=0),TODAY)
        self.assertEqual(result['scenarios'][0]['fee'],0)

    def test_zero_demand_does_not_create_order_date(self):
        result=forecast(self.product(daily=0),self.profile(),TODAY)
        self.assertNotIn('order_by',result)
        self.assertEqual(result['scenarios'],[])

    def test_no_profile_means_no_assumed_lead_time(self):
        result=forecast(self.product(),None,TODAY)
        self.assertFalse(result['configured'])
        self.assertNotIn('order_by',result)

    def test_invalid_ranges_and_nonfinite_numbers(self):
        for values in [dict(acceptance_min=11,acceptance_max=2),dict(margin=float('nan')),
                       dict(base_fee=float('inf')),dict(batch=0),dict(seasonal_factor=0),dict(wait=-1)]:
            with self.assertRaises(ValueError):
                validate_profile(self.profile(**values))

    def test_higher_seasonal_assumption_shortens_stock_cover(self):
        a=forecast(self.product(),self.profile(),TODAY)
        b=forecast(self.product(),self.profile(seasonal_factor=2),TODAY)
        self.assertAlmostEqual(a['coverage'],10)
        self.assertAlmostEqual(b['coverage'],5)

    def test_equal_scenarios_have_no_false_winner(self):
        result=forecast(self.product(),self.profile(wait=0,coefficient_wait=2),TODAY)
        self.assertEqual(result['scenarios'][0]['total_min'],result['scenarios'][1]['total_min'])
        self.assertIn('пересекаются',result['comparison'])

    def test_cost_compares_same_batch_and_horizon(self):
        result=forecast(self.product(quantity=1000),self.profile(batch=1000),TODAY)
        now,wait=result['scenarios']
        self.assertEqual(now['total_max'],2000)
        self.assertEqual(wait['total_max'],0)

    def test_wait_really_exposes_more_unserved_demand(self):
        result=forecast(self.product(quantity=20),self.profile(batch=10000),TODAY)
        now,wait=result['scenarios']
        self.assertGreater(wait['missed_min'],now['missed_min'])
        self.assertGreater(wait['missed_max'],now['missed_max'])


if __name__=='__main__':
    unittest.main()
