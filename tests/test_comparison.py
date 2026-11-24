import copy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from agebudget import InvalidPlan, audit, compare


def plan(bound=5, budget=10):
    return {'schema': 'agebudget-plan-v1',
            'nodes': [{'id': 's', 'kind': 'source', 'max_age': bound}],
            'budgets': [{'node': 's', 'max_age': budget}]}


class ComparisonTests(unittest.TestCase):
    def test_finite_and_unknown_transitions(self):
        for a, b, change, delta in [
            (5, 2, 'BOUND_LOWER', -3), (2, 5, 'BOUND_HIGHER', 3),
            (5, 5, 'BOUND_UNCHANGED', 0), (None, 5, 'BOUND_ESTABLISHED', None),
            (5, None, 'BOUND_LOST', None), (None, None, 'STILL_UNKNOWN', None),
        ]:
            with self.subTest(a=a, b=b):
                row = compare(plan(a), plan(b))['changes'][0]
                self.assertEqual((row['change'], row['bound_delta']), (change, delta))

    def test_budget_outcomes_and_exit_status_are_candidate_based(self):
        result = compare(plan(20), plan(2))
        self.assertEqual(result['before_status'], 'BOUND_EXCEEDS_BUDGET')
        self.assertEqual(result['status'], 'BOUND_WITHIN_BUDGET')
        self.assertEqual(result['checks'], [{'node': 's', 'budget': 10,
                         'before_status': 'BOUND_EXCEEDS_BUDGET',
                         'after_status': 'BOUND_WITHIN_BUDGET',
                         'before_excess': 10, 'after_excess': 0}])
        self.assertEqual(compare(plan(2), plan(None))['status'], 'UNKNOWN')

    def test_reject_changed_budget_and_targets(self):
        for candidate in [plan(2, 30), {'schema': 'agebudget-plan-v1',
                'nodes': [{'id': 't', 'kind': 'source', 'max_age': 2}],
                'budgets': [{'node': 't', 'max_age': 10}]}]:
            with self.assertRaisesRegex(InvalidPlan, 'identical budget'):
                compare(plan(), candidate)

    def test_added_removed_and_kind_changes_are_visible(self):
        before = plan()
        before['nodes'].append({'id': 'old', 'kind': 'source', 'max_age': 1})
        after = plan()
        after['nodes'] = [{'id': 'input', 'kind': 'source', 'max_age': None},
                          {'id': 's', 'kind': 'gate', 'inputs': ['input'], 'max_age': 5}]
        changes = {row['node']: row for row in compare(before, after)['changes']}
        self.assertEqual(changes['old']['change'], 'REMOVED')
        self.assertEqual(changes['input']['change'], 'ADDED')
        row = changes['s']
        self.assertEqual(row['change'], 'BOUND_UNCHANGED')
        self.assertEqual(row['before']['kind'], 'source')
        self.assertEqual(row['after']['kind'], 'gate')
        self.assertFalse(row['before']['conditional_on_gate'])
        self.assertTrue(row['after']['conditional_on_gate'])

    def test_validation_and_nonmutation(self):
        before, after = plan(), plan(None)
        saved = copy.deepcopy((before, after))
        compare(before, after)
        self.assertEqual((before, after), saved)
        for a, b in [({}, after), (before, {}), (before, {'schema': 'wrong'})]:
            with self.assertRaises(InvalidPlan):
                compare(a, b)

    def test_determinism_and_independent_chain_arithmetic(self):
        rng = random.Random(873)
        for _ in range(200):
            a, b = rng.randrange(20), rng.randrange(20)
            delay, hold = rng.randrange(20), rng.randrange(20)
            left, right = plan(a, 100), plan(b, 100)
            for p in [left, right]:
                p['nodes'].append({'id': 'out', 'kind': 'hold', 'inputs': ['s'],
                                   'max_delay': delay, 'max_residence': hold})
                p['budgets'].append({'node': 'out', 'max_age': 100})
            result = compare(left, right)
            out = next(x for x in result['changes'] if x['node'] == 'out')
            self.assertEqual(out['before']['upper_bound'], a + delay + hold)
            self.assertEqual(out['after']['upper_bound'], b + delay + hold)
            self.assertEqual(out['bound_delta'], b - a)
            left['nodes'].reverse()
            right['nodes'].reverse()
            left['budgets'].reverse()
            right['budgets'].reverse()
            self.assertEqual(compare(left, right), result)

    def test_reports_preserve_unknown_causes_and_explanation(self):
        left, right = plan(None), plan(3)
        row = compare(left, right)['changes'][0]
        self.assertEqual(row['before'], audit(left)['nodes'][0])
        self.assertEqual(row['after'], audit(right)['nodes'][0])
        self.assertEqual(row['before']['unknown_at'], ['s'])

    def test_cli_compare_and_invalid_second_input(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.json', Path(directory)/'b.json'
            for bound, code in [(20, 1), (2, 0), (None, 1)]:
                a.write_text(json.dumps(plan()), encoding='utf8')
                b.write_text(json.dumps(plan(bound)), encoding='utf8')
                proc = subprocess.run([sys.executable, '-m', 'agebudget', str(a),
                    '--compare-to', str(b)], capture_output=True)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(json.loads(proc.stdout), compare(plan(), plan(bound)))
            for content in [b'{}', b'\xff', b' '* (2*1024*1024+1),
                            b'{"schema":1,"schema":2}', json.dumps(plan(2, 20)).encode()]:
                b.write_bytes(content)
                proc = subprocess.run([sys.executable, '-m', 'agebudget', str(a),
                    '--compare-to', str(b)], capture_output=True)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, b'')
