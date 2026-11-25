import copy
import contextlib
import io
import json
import random
import unittest

from agebudget import InvalidPlan, audit, gate_reliance
from agebudget.__main__ import main


def fixture(age=20, cap=10, budget=10):
    return {'schema': 'agebudget-plan-v1', 'nodes': [
        {'id': 's', 'kind': 'source', 'max_age': age},
        {'id': 'g', 'kind': 'gate', 'inputs': ['s'], 'max_age': cap}],
        'budgets': [{'node': 'g', 'max_age': budget}]}


class GateTests(unittest.TestCase):
    def test_lost_pass_exact_fields_and_no_mutation(self):
        p = fixture()
        original = copy.deepcopy(p)
        r = gate_reliance(p, 'g')
        self.assertEqual(p, original)
        self.assertEqual(r['schema'], 'agebudget-gate-reliance-v1')
        self.assertEqual(r['declared_cap'], 10)
        self.assertEqual(r['status'], 'BOUND_EXCEEDS_BUDGET')
        self.assertEqual(r['baseline'], audit(p))
        self.assertEqual(r['checks'][0]['without_gate']['upper_bound'], 20)
        self.assertTrue(r['checks'][0]['baseline_pass_lost'])
        self.assertEqual(r['without_gate']['nodes'][0]['kind'], 'merge')
        self.assertEqual(r['changes'][0]['bound_delta'], 10)

    def test_unknown_not_infinity_or_zero(self):
        r = gate_reliance(fixture(age=None), 'g')
        self.assertEqual(r['status'], 'UNKNOWN')
        self.assertTrue(r['checks'][0]['baseline_pass_lost'])
        self.assertIsNone(r['checks'][0]['without_gate']['upper_bound'])
        self.assertEqual(r['checks'][0]['without_gate']['unknown_at'], ['s'])
        self.assertEqual(r['changes'][0]['change'], 'BOUND_LOST')
        self.assertIsNone(r['changes'][0]['bound_delta'])

    def test_equal_cap_inactive_and_failing_baselines(self):
        for age, cap, budget, expected in ((10, 10, 10, False), (2, 10, 10, False),
                                           (20, 10, 5, False), (0, 0, 0, False)):
            r = gate_reliance(fixture(age, cap, budget), 'g')
            self.assertEqual(r['checks'][0]['baseline_pass_lost'], expected)

    def test_other_gate_retained_multi_budget(self):
        p = fixture(age=None)
        p['nodes'].append({'id': 'h', 'kind': 'gate', 'inputs': ['g'], 'max_age': 3})
        p['budgets'].append({'node': 'h', 'max_age': 3})
        r = gate_reliance(p, 'g')
        self.assertEqual([c['baseline_pass_lost'] for c in r['checks']], [True, False])
        self.assertEqual(r['checks'][1]['without_gate']['upper_bound'], 3)
        self.assertTrue(r['checks'][1]['without_gate']['conditional_on_gate'])
        self.assertEqual(r['status'], 'UNKNOWN')

    def test_all_inputs_merge_max_not_sum(self):
        p = fixture(age=2)
        p['nodes'].append({'id': 'b', 'kind': 'source', 'max_age': 30})
        p['nodes'][1]['inputs'].append('b')
        r = gate_reliance(p, 'g')
        self.assertEqual(r['checks'][0]['without_gate']['upper_bound'], 30)

    def test_unrelated_gate_and_unrelated_unknown_budget(self):
        p = fixture()
        p['nodes'].append({'id': 'other', 'kind': 'source', 'max_age': None})
        p['budgets'] = [{'node': 's', 'max_age': 30}, {'node': 'other', 'max_age': 2}]
        r = gate_reliance(p, 'g')
        self.assertEqual(r['status'], 'UNKNOWN')
        self.assertFalse(any(c['baseline_pass_lost'] for c in r['checks']))

    def test_invalid_selection_and_full_graph_validation(self):
        for target in ('missing', 's', None, [], True):
            with self.subTest(target=target), self.assertRaises(InvalidPlan):
                gate_reliance(fixture(), target)
        p = fixture()
        p['nodes'].append({'id': 'cycle', 'kind': 'merge', 'inputs': ['cycle']})
        with self.assertRaises(InvalidPlan):
            gate_reliance(p, 'g')

    def test_seeded_possibility_set_oracle(self):
        # Independent set enumeration, including empty gate rejection sets.
        # Upper-bound semantics are conditional, so gates can have a finite cap
        # even when no enumerated value survives. For reachable finite examples
        # below, each source includes zero, ensuring nonempty gate outputs.
        rng = random.Random(822)
        for case in range(160):
            nodes = [{'id': 's', 'kind': 'source', 'max_age': rng.randrange(5)}]
            for i in range(6):
                deps = rng.sample([n['id'] for n in nodes], rng.randint(1, min(2, len(nodes))))
                kind = 'gate' if i == 0 else rng.choice(['gate', 'merge', 'hold'])
                n = {'id': str(i), 'kind': kind, 'inputs': deps}
                if kind == 'gate':
                    n['max_age'] = rng.randrange(12)
                if kind == 'hold':
                    n.update(max_delay=rng.randrange(3), max_residence=rng.randrange(3))
                nodes.append(n)
            p = {'schema': 'agebudget-plan-v1', 'nodes': nodes,
                 'budgets': [{'node': str(i), 'max_age': rng.randrange(12)} for i in range(6)]}
            by_id = {n['id']: n for n in nodes}
            def values(key, bypass):
                n = by_id[key]
                if n['kind'] == 'source':
                    return set(range(n['max_age']+1))
                sets = [values(d, bypass) for d in n['inputs']]
                result = {0}
                for incoming in sets:
                    result = {max(a,b) for a in result for b in incoming}
                if n['kind'] == 'hold':
                    result = {v+d+r for v in result for d in range(n['max_delay']+1)
                              for r in range(n['max_residence']+1)}
                if n['kind'] == 'gate' and key != bypass:
                    result = {v for v in result if v <= n['max_age']}
                return result
            for gate in (n['id'] for n in nodes if n['kind'] == 'gate'):
                r = gate_reliance(p, gate)
                for row in r['checks']:
                    before, after = max(values(row['node'], None)), max(values(row['node'], gate))
                    self.assertEqual(row['baseline']['upper_bound'], before, case)
                    self.assertEqual(row['without_gate']['upper_bound'], after, case)
                    self.assertEqual(row['baseline_pass_lost'], before <= row['budget'] < after, case)
                shuffled = copy.deepcopy(p)
                rng.shuffle(shuffled['nodes']); rng.shuffle(shuffled['budgets'])
                for n in shuffled['nodes']:
                    if 'inputs' in n:
                        rng.shuffle(n['inputs'])
                self.assertEqual(gate_reliance(shuffled, gate), r)

    def test_cli_statuses_exclusive_modes_and_no_invalid_report(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            for age, code in ((20, 1), (2, 0), (None, 1)):
                path.write_text(json.dumps(fixture(age=age)))
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main([str(path), '--without-gate', 'g']), code)
                self.assertEqual(json.loads(out.getvalue()), gate_reliance(fixture(age=age), 'g'))
            for extra in (['--headroom', 's'], ['--compare-to', str(path)], []):
                with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                    target = 'missing' if not extra else 'g'
                    self.assertEqual(main([str(path), '--without-gate', target, *extra]), 2)
                self.assertEqual(out.getvalue(), '')

    def test_all_admitted_nodes_and_large_exact_bounds(self):
        from agebudget.core import MAX_TICKS
        nodes = [{'id': 's', 'kind': 'source', 'max_age': MAX_TICKS}]
        previous = 's'
        for i in range(510):
            name = 'h' + str(i)
            nodes.append({'id': name, 'kind': 'hold', 'inputs': [previous],
                          'max_delay': MAX_TICKS, 'max_residence': MAX_TICKS})
            previous = name
        nodes.append({'id': 'g', 'kind': 'gate', 'inputs': [previous], 'max_age': 0})
        p = {'schema': 'agebudget-plan-v1', 'nodes': nodes,
             'budgets': [{'node': 'g', 'max_age': MAX_TICKS}]}
        r = gate_reliance(p, 'g')
        self.assertEqual(r['checks'][0]['without_gate']['upper_bound'], 1021 * MAX_TICKS)
        self.assertTrue(r['checks'][0]['baseline_pass_lost'])

    def test_output_boundary_covers_new_mode(self):
        import tempfile
        from pathlib import Path
        class Short(io.StringIO):
            def write(self, text):
                super().write(text[:2])
                return 2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'p.json'
            path.write_text(json.dumps(fixture()))
            with contextlib.redirect_stdout(Short()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(path), '--without-gate', 'g']), 2)
