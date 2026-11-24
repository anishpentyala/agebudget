"""Conservative propagation of declared source-age upper bounds."""
from __future__ import annotations

import json
from collections import deque

MAX_NODES = 512
MAX_EDGES = 8192
MAX_TICKS = 10**15


class InvalidPlan(ValueError):
    """The input does not describe the supported finite model."""


def _integer(value, where, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_TICKS:
        raise InvalidPlan(f"{where}: expected integer ticks from 0 to {MAX_TICKS}")
    return value


def _keys(value, required, where):
    if type(value) is not dict or set(value) != set(required):
        raise InvalidPlan(f"{where}: required fields are {sorted(required)}")


def _name(value, where):
    if type(value) is not str or not value or len(value) > 128:
        raise InvalidPlan(f"{where}: expected nonempty string of at most 128 characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InvalidPlan(f"{where}: control characters are not permitted")
    return value


def _validate(plan):
    _keys(plan, {'schema', 'nodes', 'budgets'}, 'plan')
    if plan['schema'] != 'agebudget-plan-v1':
        raise InvalidPlan('unsupported plan schema')
    nodes = plan['nodes']
    if type(nodes) is not list or not 1 <= len(nodes) <= MAX_NODES:
        raise InvalidPlan(f'nodes: expected 1 to {MAX_NODES} nodes')
    indexed = {}
    edges = 0
    for node in nodes:
        if type(node) is not dict:
            raise InvalidPlan('node: expected object')
        kind = node.get('kind')
        fields = {
            'source': {'id', 'kind', 'max_age'},
            'hold': {'id', 'kind', 'inputs', 'max_delay', 'max_residence'},
            'merge': {'id', 'kind', 'inputs'},
            'gate': {'id', 'kind', 'inputs', 'max_age'},
        }.get(kind) if type(kind) is str else None
        if fields is None:
            raise InvalidPlan('node: unsupported kind')
        _keys(node, fields, 'node')
        name = _name(node['id'], 'node.id')
        if name in indexed:
            raise InvalidPlan(f'duplicate node: {name}')
        indexed[name] = node
        if kind in ('source', 'gate'):
            _integer(node['max_age'], f'{name}.max_age', optional=kind == 'source')
        if kind == 'hold':
            _integer(node['max_delay'], f'{name}.max_delay', optional=True)
            _integer(node['max_residence'], f'{name}.max_residence', optional=True)
        if kind != 'source':
            inputs = node['inputs']
            if type(inputs) is not list or not inputs:
                raise InvalidPlan(f'{name}.inputs: expected nonempty list')
            for dependency in inputs:
                _name(dependency, f'{name}.inputs')
            if len(set(inputs)) != len(inputs):
                raise InvalidPlan(f'{name}.inputs: duplicate dependency')
            edges += len(inputs)
            if edges > MAX_EDGES:
                raise InvalidPlan('too many dependency edges')
    children = {name: [] for name in indexed}
    degrees = {}
    for name, node in indexed.items():
        inputs = node.get('inputs', [])
        degrees[name] = len(inputs)
        for dependency in inputs:
            if dependency not in indexed:
                raise InvalidPlan(f'{name}: missing dependency {dependency}')
            children[dependency].append(name)
    queue = deque(sorted(name for name in indexed if degrees[name] == 0))
    order = []
    while queue:
        name = queue.popleft()
        order.append(name)
        for child in sorted(children[name]):
            degrees[child] -= 1
            if degrees[child] == 0:
                queue.append(child)
    if len(order) != len(indexed):
        raise InvalidPlan('dependency cycle')
    budgets = plan['budgets']
    if type(budgets) is not list or not 1 <= len(budgets) <= MAX_NODES:
        raise InvalidPlan('budgets: expected nonempty bounded list')
    seen = set()
    for budget in budgets:
        _keys(budget, {'node', 'max_age'}, 'budget')
        name = _name(budget['node'], 'budget.node')
        if name not in indexed or name in seen:
            raise InvalidPlan('budget: unknown or repeated node')
        seen.add(name)
        _integer(budget['max_age'], 'budget.max_age')
    return indexed, order


def audit(plan):
    """Return conditional upper bounds, not observed ages or availability proof.

    Unknown input bounds propagate through holds and merges. A gate establishes
    its declared cap even for unknown input, conditional on rejecting all older
    data rather than returning it as fallback. All inputs are nonnegative integer
    ticks in one caller-chosen unit. Bounds include their endpoints.
    """
    indexed, order = _validate(plan)
    results = {}
    for name in order:
        node = indexed[name]
        kind = node['kind']
        if kind == 'source':
            bound = node['max_age']
            path = [name]
            unknown = [name] if bound is None else []
        else:
            inputs = sorted(node['inputs'])
            unknown = sorted(set(x for key in inputs for x in results[key]['unknown_at']))
            unknown_inputs = [key for key in inputs if results[key]['upper_bound'] is None]
            chosen = unknown_inputs[0] if unknown_inputs else min(
                inputs, key=lambda key: (-results[key]['upper_bound'], key))
            bound = None if unknown_inputs else results[chosen]['upper_bound']
            path = results[chosen]['explanation_path'] + [name]
            if kind == 'hold':
                delay, residence = node['max_delay'], node['max_residence']
                if delay is None or residence is None:
                    unknown = sorted(set(unknown + [name]))
                    bound = None
                elif bound is not None:
                    bound += delay + residence
            elif kind == 'gate':
                cap = node['max_age']
                if bound is None or cap < bound:
                    path = [name]
                bound = cap if bound is None else min(bound, cap)
                unknown = []
        results[name] = {
            'node': name, 'kind': kind, 'upper_bound': bound,
            'explanation_path': path, 'unknown_at': unknown,
            'conditional_on_gate': kind == 'gate' or any(
                results[key]['conditional_on_gate'] for key in node.get('inputs', [])),
        }
    checks = []
    for budget in sorted(plan['budgets'], key=lambda row: row['node']):
        result = results[budget['node']]
        bound = result['upper_bound']
        status = 'UNKNOWN' if bound is None else (
            'BOUND_WITHIN_BUDGET' if bound <= budget['max_age'] else 'BOUND_EXCEEDS_BUDGET')
        checks.append({**result, 'budget': budget['max_age'], 'status': status,
                       'bound_excess': None if bound is None else max(0, bound - budget['max_age'])})
    status = 'UNKNOWN' if any(c['status'] == 'UNKNOWN' for c in checks) else (
        'BOUND_EXCEEDS_BUDGET' if any(c['status'] == 'BOUND_EXCEEDS_BUDGET' for c in checks)
        else 'BOUND_WITHIN_BUDGET')
    return {'schema': 'agebudget-report-v1', 'status': status,
            'nodes': [results[key] for key in sorted(results)], 'checks': checks,
            'limitations': [
                'Declared conditional upper bounds, not observed source freshness.',
                'A bound exceeding a budget is not proof an actual response violates it.',
                'Gates may reject every value; bounded returned age does not prove availability.',
                'No HTTP header parsing, revalidation, stale fallback, clock or runtime verification.',
            ]}


def loads(text):
    """Read strict JSON, rejecting repeated object keys and nonfinite numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidPlan(f'duplicate JSON key: {key}')
            result[key] = value
        return result

    def constant(value):
        raise InvalidPlan(f'nonfinite JSON number: {value}')

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as exc:
        raise InvalidPlan(str(exc)) from exc
