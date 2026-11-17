"""Token usage parsing and integer microcredit summaries."""
import unittest
from orchpact.credits import token_count,measure,summarize

class CreditTests(unittest.TestCase):
    def test_usage_formats_and_unknown_values(self):
        for value,expected in [({'total_tokens':1200},1200),({'prompt_tokens':800,'completion_tokens':400},1200),({'input_tokens':100,'output_tokens':20},120),({'totalTokenCount':42},42),({'total_tokens':0},0),({},None),({'input_tokens':10},None),({'total_tokens':-1},None),({'total_tokens':True},None),({'total_tokens':'1000'},None),({'total_tokens':1.5},None),({'total_tokens':float('inf')},None),([],None)]:
            with self.subTest(value=value):self.assertEqual(token_count(value),expected)
    def test_missing_usage_does_not_become_zero(self):
        row=dict(kind='model',usage='{}',status='unknown',credit_policy=None)
        self.assertIsNone(measure(row)['points']);self.assertFalse(summarize([row])['complete'])
    def test_fractional_sum_uses_integer_microcredits(self):
        rows=[dict(kind='model',usage='{"total_tokens":100}',status='completed',credit_policy={"tokens_per_point":1000,"search_points":10}) for _ in range(10)]
        self.assertEqual(summarize(rows)['points'],1)

    def test_missing_policy_is_unmeasurable_even_with_known_tokens(self):
        row=dict(kind='model',usage={'total_tokens':1000},status='completed',credit_policy=None)
        value=measure(row)
        self.assertEqual(value['status'],'missing_policy')
        self.assertIsNone(value['points'])
        self.assertFalse(summarize([row])['complete'])
