"""Independent negative cases for the multi-warehouse delivery example."""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import unittest

ROOT=Path(__file__).resolve().parents[1]
CHECK=runpy.run_path(str(ROOT/'examples/fulfillment/verify.py'))['check_plan']


class FulfillmentExampleTests(unittest.TestCase):
    def setUp(self):
        self.original=json.loads((ROOT/'examples/fulfillment/input.json').read_text())
        self.plan=json.loads((ROOT/'examples/fulfillment/feasible-plan.json').read_text())

    def test_reference_plan_fulfills_all_fifty_units_within_budget(self):
        result=CHECK(self.original,self.plan)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['total_cost_cents'],1230)
        self.assertEqual(sum(row['units'] for row in result['deliveries']),50)
        self.assertEqual(len(result['deliveries']),8)

    def test_different_feasible_schedule_is_accepted_without_matching_reference_bytes(self):
        alternate=deepcopy(self.plan)
        next(row for row in alternate['shipments'] if row['order_id']=='O7' and row['sku']=='A')['ship_day']=3
        self.assertNotEqual(alternate,self.plan)
        self.assertEqual(CHECK(self.original,alternate)['status'],'PASS')

    def test_each_hard_constraint_rejects_a_counterexample(self):
        cases=[]
        def case(code,mutate):
            plan=deepcopy(self.plan);mutate(plan);cases.append((code,plan))
        case('unknown_reference',lambda plan:plan['shipments'][0].update(order_id='unknown'))
        case('unknown_sku',lambda plan:plan['shipments'][0].update(sku='D'))
        case('duplicate_shipment',lambda plan:plan['shipments'][1].update(shipment_id=plan['shipments'][0]['shipment_id']))
        case('order_quantity',lambda plan:plan['shipments'][0].update(quantity=5))
        case('inventory',lambda plan:next(row for row in plan['shipments'] if row['order_id']=='O4' and row['sku']=='A').update(ship_day=1))
        case('warehouse_capacity',lambda plan:[row.update(ship_day=2) for row in plan['shipments'] if row['order_id']=='O7'])
        case('carrier_capacity',lambda plan:[row.update(ship_day=3) for row in plan['shipments'] if row['order_id']=='O7'])
        case('deadline',lambda plan:[row.update(ship_day=2) for row in plan['shipments'] if row['order_id']=='O1'])
        case('budget',lambda plan:plan['shipments'][0].update(carrier='express'))
        case('schema',lambda plan:plan['shipments'][0].update(quantity=-1))
        for code,plan in cases:
            with self.subTest(code=code):
                result=CHECK(self.original,plan)
                self.assertEqual(result['status'],'FAIL')
                self.assertIn(code,{row['code'] for row in result['violations']})

    def test_missing_tariff_confirmation_does_not_turn_into_pass(self):
        self.original['rate_confirmation']='pending'
        self.assertEqual(CHECK(self.original,self.plan)['status'],'INCONCLUSIVE')
        self.original['budget_cents']=1000
        self.assertEqual(CHECK(self.original,self.plan)['status'],'FAIL')

    def test_ambiguous_original_ids_or_rates_are_inconclusive(self):
        self.original['routes'].append(deepcopy(self.original['routes'][0]))
        self.assertEqual(CHECK(self.original,self.plan)['status'],'INCONCLUSIVE')
        self.original['routes'].pop()
        self.original['orders'][1]['id']=self.original['orders'][0]['id']
        self.assertEqual(CHECK(self.original,self.plan)['status'],'INCONCLUSIVE')

    def test_reference_solution_is_not_used_by_the_verifier(self):
        # The verifier reads its schema and the provided input/candidate only.
        from unittest.mock import patch
        original_read=Path.read_text
        def read(path,*args,**kwargs):
            self.assertNotEqual(path.name,'feasible-plan.json')
            return original_read(path,*args,**kwargs)
        with patch.object(Path,'read_text',read):self.assertEqual(CHECK(self.original,self.plan)['status'],'PASS')
