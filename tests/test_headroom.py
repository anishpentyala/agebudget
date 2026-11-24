import copy
import io
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from agebudget import InvalidPlan, residence_headroom
from agebudget.core import MAX_TICKS
from test_core import plan, source, hold


class HeadroomTests(unittest.TestCase):
    def test_exact_chain_and_input_unchanged(self):
        p = plan([source(age=3), hold(delay=2, residence=4)], budget=12)
        original = copy.deepcopy(p)
        r = residence_headroom(p, 'cache')
        self.assertEqual((r['max_residence'], r['additional_residence']), (7, 3))
        self.assertFalse(r['domain_limited'])
        self.assertEqual(r['at_limit']['checks'][0]['upper_bound'], 12)
        self.assertEqual(r['next_tick']['checks'][0]['upper_bound'], 13)
        self.assertEqual(r['next_tick']['status'], 'BOUND_EXCEEDS_BUDGET')
        self.assertEqual(p, original)

    def test_zero_headroom(self):
        r = residence_headroom(plan(), 'cache')
        self.assertEqual(r['additional_residence'], 0)
        self.assertEqual(r['max_residence'], 10)

    def test_multiple_budgets_tightest_target(self):
        p = plan([source(), hold(residence=1), hold('end', ['cache'], 3, 2)], budget=50)
        p['budgets'].append({'node': 'end', 'max_age': 12})
        r = residence_headroom(p, 'cache')
        self.assertEqual(r['max_residence'], 7)
        self.assertEqual([c['status'] for c in r['next_tick']['checks']],
                         ['BOUND_WITHIN_BUDGET', 'BOUND_EXCEEDS_BUDGET'])

    def test_gate_shielding_and_irrelevance_are_domain_limited(self):
        p = plan([source(age=None), hold(residence=1),
                  {'id': 'gate', 'kind': 'gate', 'inputs': ['cache'], 'max_age': 2}], 'gate', 2)
        for selected in [p, plan([source(), hold(residence=1)], 'origin', 0)]:
            r = residence_headroom(selected, 'cache')
            self.assertTrue(r['domain_limited'])
            self.assertEqual(r['max_residence'], MAX_TICKS)
            self.assertIsNone(r['next_tick'])
            self.assertEqual(r['baseline']['status'], 'BOUND_WITHIN_BUDGET')

    def test_gate_above_budget_still_constrains(self):
        p = plan([source(), hold(residence=1),
                  {'id': 'gate', 'kind': 'gate', 'inputs': ['cache'], 'max_age': 20}], 'gate', 8)
        self.assertEqual(residence_headroom(p, 'cache')['max_residence'], 8)

    def test_parallel_merge_does_not_sum(self):
        p = plan([source('a', 2), source('b', 8), hold(inputs=['a'], residence=1),
                  {'id': 'end', 'kind': 'merge', 'inputs': ['cache', 'b']}], 'end', 12)
        self.assertEqual(residence_headroom(p, 'cache')['max_residence'], 10)

    def test_maximum_and_large_exact_arithmetic(self):
        p = plan([source(), hold(residence=MAX_TICKS)], budget=MAX_TICKS)
        r = residence_headroom(p, 'cache')
        self.assertEqual(r['additional_residence'], 0)
        self.assertTrue(r['domain_limited'])
        p = plan([source(age=MAX_TICKS-2), hold(residence=1)], budget=MAX_TICKS)
        self.assertEqual(residence_headroom(p, 'cache')['max_residence'], 2)

    def test_invalid_and_unestablished_baselines_refused(self):
        cases = [(plan(), 'absent'), (plan(), 'origin'), (plan(), []),
                 (plan([source(), hold(residence=None)]), 'cache'),
                 (plan([source(age=None), hold()]), 'cache'),
                 (plan(budget=0), 'cache'), ({}, 'cache')]
        for p, node in cases:
            with self.subTest(node=node), self.assertRaises(InvalidPlan):
                residence_headroom(p, node)

    def test_unrelated_unknown_budget_is_not_ignored(self):
        p = plan([source(), hold(residence=1), source('other', None)])
        p['budgets'].append({'node': 'other', 'max_age': 10})
        with self.assertRaises(InvalidPlan):
            residence_headroom(p, 'cache')

    def test_monotone_search_has_bounded_evaluations(self):
        import agebudget.headroom as module
        with mock.patch.object(module, 'audit', wraps=module.audit) as call:
            residence_headroom(plan(budget=MAX_TICKS), 'cache')
        self.assertLessEqual(call.call_count, 53)

    def test_seeded_independent_path_enumeration_oracle(self):
        # Enumerate every source-to-target path, adding holds and capping at
        # gates. Compare every candidate in a small numeric domain exhaustively.
        # All nodes have finite fields. A direct budget ensures a finite answer.
        rng = random.Random(5271)
        for case in range(120):
            nodes = [source('s', rng.randrange(3)), hold('h', ['s'], rng.randrange(3), 0)]
            for i in range(5):
                deps = rng.sample([n['id'] for n in nodes], rng.randint(1, min(3, len(nodes))))
                kind = rng.choice(['merge', 'gate', 'hold'])
                row = {'id': str(i), 'kind': kind, 'inputs': deps}
                if kind == 'gate':
                    row['max_age'] = rng.randrange(15)
                if kind == 'hold':
                    row.update(max_delay=rng.randrange(3), max_residence=rng.randrange(3))
                nodes.append(row)
            indexed = {row['id']: row for row in nodes}

            def paths(name, residence):
                n = indexed[name]
                if n['kind'] == 'source':
                    return [n['max_age']]
                values = [v for d in n['inputs'] for v in paths(d, residence)]
                if n['kind'] == 'hold':
                    return [v+n['max_delay']+(residence if name == 'h' else n['max_residence']) for v in values]
                if n['kind'] == 'gate':
                    return [min(v, n['max_age']) for v in values]
                return values

            budgets = [{'node': 'h', 'max_age': max(paths('h', 0))+rng.randrange(16)},
                       {'node': '4', 'max_age': max(paths('4', 0))+rng.randrange(12)}]
            p = {'schema': 'agebudget-plan-v1', 'nodes': nodes, 'budgets': budgets}
            accepted = [x for x in range(16) if all(max(paths(b['node'], x)) <= b['max_age'] for b in budgets)]
            expected = max(accepted)
            r = residence_headroom(p, 'h')
            self.assertEqual(r['max_residence'], expected, case)
            permuted = copy.deepcopy(p)
            rng.shuffle(permuted['nodes'])
            rng.shuffle(permuted['budgets'])
            for row in permuted['nodes']:
                if 'inputs' in row:
                    rng.shuffle(row['inputs'])
            self.assertEqual(residence_headroom(permuted, 'h'), r, case)

    def test_maximum_depth_and_size(self):
        nodes = [source('0')]
        for i in range(1, 512):
            nodes.append(hold(str(i), [str(i-1)], 0, 0))
        p = plan(nodes, '511', 123)
        self.assertEqual(residence_headroom(p, '256')['max_residence'], 123)

    def test_cli_modes_and_failed_delivery(self):
        from agebudget.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'p.json'
            path.write_text(json.dumps(plan()))
            for args, code in [(['--headroom', 'cache'], 0), (['--headroom', 'origin'], 2),
                               (['--headroom', 'cache', '--compare-to', str(path)], 2)]:
                result = subprocess.run([sys.executable, '-m', 'agebudget', str(path), *args], capture_output=True)
                self.assertEqual(result.returncode, code, result.stderr)
                if code == 0:
                    self.assertEqual(json.loads(result.stdout)['schema'], 'agebudget-residence-headroom-v1')
                else:
                    self.assertEqual(result.stdout, b'')
            class Short(io.StringIO):
                def write(self, value):
                    return 0
            with mock.patch('sys.stdout', Short()), mock.patch('sys.stderr', io.StringIO()):
                self.assertEqual(main([str(path), '--headroom', 'cache']), 2)
