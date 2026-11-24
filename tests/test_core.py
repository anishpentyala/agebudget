import copy
import itertools
import json
import random
import unittest

from agebudget import InvalidPlan, audit, loads


def source(name='origin', age=0):
    return {'id': name, 'kind': 'source', 'max_age': age}


def hold(name='cache', inputs=None, delay=0, residence=10):
    return {'id': name, 'kind': 'hold', 'inputs': inputs or ['origin'],
            'max_delay': delay, 'max_residence': residence}


def plan(nodes=None, target='cache', budget=10):
    return {'schema': 'agebudget-plan-v1', 'nodes': nodes or [source(), hold()],
            'budgets': [{'node': target, 'max_age': budget}]}


def check(p):
    return audit(p)['checks'][0]


class CoreTests(unittest.TestCase):
    def test_composed_residence(self):
        p = plan([source(age=3), hold(delay=2, residence=5),
                  hold('browser', ['cache'], 1, 7)], 'browser', 12)
        r = check(p)
        self.assertEqual(r['upper_bound'], 18)
        self.assertEqual(r['bound_excess'], 6)
        self.assertEqual(r['explanation_path'], ['origin', 'cache', 'browser'])
        self.assertEqual(r['status'], 'BOUND_EXCEEDS_BUDGET')

    def test_inclusive_equality(self):
        self.assertEqual(check(plan())['status'], 'BOUND_WITHIN_BUDGET')

    def test_unknown_source(self):
        r = check(plan([source(age=None), hold()]))
        self.assertIsNone(r['upper_bound'])
        self.assertEqual(r['unknown_at'], ['origin'])
        self.assertEqual(r['status'], 'UNKNOWN')

    def test_unknown_hold_dimensions(self):
        for delay, residence in [(None, 1), (1, None), (None, None)]:
            with self.subTest(delay=delay, residence=residence):
                r = check(plan([source(), hold(delay=delay, residence=residence)]))
                self.assertEqual(r['unknown_at'], ['cache'])
                self.assertIsNone(r['upper_bound'])

    def test_gate_caps_unknown(self):
        p = plan([source(age=None), {'id': 'gate', 'kind': 'gate',
                  'inputs': ['origin'], 'max_age': 4}, hold(inputs=['gate'])])
        r = check(p)
        self.assertEqual(r['upper_bound'], 14)
        self.assertEqual(r['unknown_at'], [])
        self.assertEqual(r['explanation_path'], ['gate', 'cache'])
        self.assertTrue(r['conditional_on_gate'])

    def test_gate_does_not_increase_bound(self):
        p = plan([source(age=2), {'id': 'gate', 'kind': 'gate',
                  'inputs': ['origin'], 'max_age': 100}], 'gate')
        self.assertEqual(check(p)['upper_bound'], 2)
        self.assertEqual(check(p)['explanation_path'], ['origin', 'gate'])

    def test_zero_gate_not_availability(self):
        r = check(plan([source(age=None), {'id': 'gate', 'kind': 'gate',
                  'inputs': ['origin'], 'max_age': 0}], 'gate', 0))
        self.assertEqual(r['status'], 'BOUND_WITHIN_BUDGET')
        self.assertTrue(r['conditional_on_gate'])

    def test_merge_takes_oldest_bound_not_sum(self):
        p = plan([source('a', 5), source('b', 8),
                  {'id': 'merge', 'kind': 'merge', 'inputs': ['a', 'b']}], 'merge')
        self.assertEqual(check(p)['upper_bound'], 8)
        self.assertEqual(check(p)['explanation_path'], ['b', 'merge'])

    def test_unknown_branch_is_not_ignored(self):
        p = plan([source('a', None), source('b', 8), hold(inputs=['a', 'b'])])
        self.assertIsNone(check(p)['upper_bound'])

    def test_tie_deterministic(self):
        p = plan([source('z', 8), source('a', 8), hold(inputs=['z', 'a'])])
        self.assertEqual(check(p)['explanation_path'], ['a', 'cache'])
        p['nodes'].reverse()
        p['nodes'][0]['inputs'].reverse()
        self.assertEqual(check(p)['explanation_path'], ['a', 'cache'])

    def test_no_mutation(self):
        p = plan()
        before = copy.deepcopy(p)
        audit(p)
        self.assertEqual(p, before)

    def test_long_nonrecursive_chain(self):
        nodes = [source('0')]
        for i in range(1, 512):
            nodes.append(hold(str(i), [str(i-1)], 1, 2))
        self.assertEqual(check(plan(nodes, '511', 2000))['upper_bound'], 1533)

    def test_large_integer_exact(self):
        p = plan([source(age=10**15), hold(delay=10**15, residence=10**15)], budget=10**15)
        self.assertEqual(check(p)['upper_bound'], 3*10**15)

    def test_invalid_shapes(self):
        bad = [None, [], {}, {'schema': 'agebudget-plan-v1', 'nodes': [], 'budgets': []}]
        for p in bad:
            with self.subTest(p=p), self.assertRaises(InvalidPlan):
                audit(p)

    def test_unknown_keys(self):
        for where in ['plan', 'node', 'budget']:
            p = plan()
            target = p if where == 'plan' else p['nodes'][0] if where == 'node' else p['budgets'][0]
            target['typo'] = 0
            with self.subTest(where=where), self.assertRaises(InvalidPlan):
                audit(p)

    def test_bad_numbers(self):
        for value in [True, False, -1, 1.0, '1', float('inf'), 10**15+1, []]:
            with self.subTest(value=value), self.assertRaises(InvalidPlan):
                audit(plan([source(age=value), hold()]))

    def test_bad_names(self):
        for name in ['', '\n', 'a'*129, [], 1]:
            p = plan()
            p['nodes'][0]['id'] = name
            with self.subTest(name=name), self.assertRaises(InvalidPlan):
                audit(p)

    def test_duplicate_node(self):
        with self.assertRaises(InvalidPlan):
            audit(plan([source(), source()], 'origin'))

    def test_cycle_and_missing_dependency(self):
        for deps in [['cache'], ['missing'], ['origin', 'origin']]:
            with self.subTest(deps=deps), self.assertRaises(InvalidPlan):
                audit(plan([source(), hold(inputs=deps)]))

    def test_disconnected_cycle_rejected(self):
        p = plan([source(), hold(), hold('x', ['y']), hold('y', ['x'])])
        with self.assertRaises(InvalidPlan):
            audit(p)

    def test_bad_budgets(self):
        for budgets in [[], {}, [{'node': 'absent', 'max_age': 1}],
                        [{'node': 'origin', 'max_age': None}],
                        [{'node': 'origin', 'max_age': 1}]*2]:
            p = plan()
            p['budgets'] = budgets
            with self.subTest(budgets=budgets), self.assertRaises(InvalidPlan):
                audit(p)

    def test_bad_inputs(self):
        for inputs in [[], 'origin', [None], [[]]]:
            p = plan()
            p['nodes'][1]['inputs'] = inputs
            with self.subTest(inputs=inputs), self.assertRaises(InvalidPlan):
                audit(p)

    def test_strict_json(self):
        for text in ['{"a":1,"a":2}', '{"x": NaN}', '{"x": Infinity}', '{']:
            with self.subTest(text=text), self.assertRaises(InvalidPlan):
                loads(text)
        self.assertEqual(loads(json.dumps(plan())), plan())

    def test_enumerated_local_ages_oracle(self):
        # Independent finite possibility-set propagation, not the production recurrence.
        rng = random.Random(391)
        for case in range(300):
            nodes = [source('0', rng.randrange(4)), source('1', rng.randrange(4))]
            possible = {n['id']: set(range(n['max_age']+1)) for n in nodes}
            for i in range(2, 8):
                name = str(i)
                deps = rng.sample(list(possible), rng.randint(1, min(2, len(possible))))
                input_ages = {max(ages) for ages in itertools.product(*(possible[d] for d in deps))}
                kind = rng.choice(['hold', 'merge', 'gate'])
                if kind == 'hold':
                    delay, residence = rng.randrange(3), rng.randrange(3)
                    node = hold(name, deps, delay, residence)
                    possible[name] = {age+d+r for age in input_ages for d in range(delay+1) for r in range(residence+1)}
                elif kind == 'gate':
                    cap = rng.randrange(6)
                    node = {'id': name, 'kind': kind, 'inputs': deps, 'max_age': cap}
                    possible[name] = {age for age in input_ages if age <= cap}
                else:
                    node = {'id': name, 'kind': kind, 'inputs': deps}
                    possible[name] = input_ages
                nodes.append(node)
            report = audit(plan(nodes, '7', 10))
            for result in report['nodes']:
                with self.subTest(case=case, node=result['node']):
                    self.assertEqual(result['upper_bound'], max(possible[result['node']]))

class MoreContracts(unittest.TestCase):
    def test_mixed_checks_are_retained(self):
        p = plan([source('unknown', None), source('old', 20)])
        p['budgets'] = [{'node': 'unknown', 'max_age': 5}, {'node': 'old', 'max_age': 5}]
        r = audit(p)
        self.assertEqual(r['status'], 'UNKNOWN')
        self.assertEqual({c['node']: c['status'] for c in r['checks']},
                         {'old': 'BOUND_EXCEEDS_BUDGET', 'unknown': 'UNKNOWN'})

    def test_gate_caveat_survives_noncritical_branch(self):
        p = plan([source('a', 1), source('b', 10),
                  {'id': 'gate', 'kind': 'gate', 'inputs': ['a'], 'max_age': 1},
                  hold(inputs=['gate', 'b'])])
        r = check(p)
        self.assertEqual(r['explanation_path'], ['b', 'cache'])
        self.assertTrue(r['conditional_on_gate'])

    def test_malformed_kinds(self):
        for kind in [None, [], {}, 'unknown']:
            p = plan()
            p['nodes'][0]['kind'] = kind
            with self.subTest(kind=kind), self.assertRaises(InvalidPlan):
                audit(p)

    def test_gate_rejects_unknown_cap(self):
        with self.assertRaises(InvalidPlan):
            audit(plan([source(), {'id': 'cache', 'kind': 'gate', 'inputs': ['origin'], 'max_age': None}]))

    def test_512_node_limit(self):
        with self.assertRaises(InvalidPlan):
            audit(plan([source(str(i)) for i in range(513)], '0'))

    def test_all_node_permutations_identical(self):
        nodes = [source('a', 3), source('b', 4), hold('c', ['a', 'b']),
                 {'id': 'd', 'kind': 'gate', 'inputs': ['c'], 'max_age': 8}]
        expected = audit(plan(nodes, 'd'))
        for perm in itertools.permutations(nodes):
            self.assertEqual(audit(plan(list(perm), 'd')), expected)
