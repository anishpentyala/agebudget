"""One-gate counterfactuals under otherwise fixed declared assumptions."""
from copy import deepcopy

from .comparison import compare
from .core import InvalidPlan, audit


def gate_reliance(plan, gate):
    """Audit bypass of one rejecting gate, not removal of a runtime component.

    The selected gate becomes an immediate merge of the same inputs. No age is
    added, and all other gates, budgets and assumptions are retained. This is
    not evidence about what happens when a real system's enforcement fails.
    """
    baseline = audit(plan)
    if type(gate) is not str:
        raise InvalidPlan('gate reliance requires an existing gate ID')
    modified = deepcopy(plan)
    selected = next((n for n in modified['nodes'] if n['id'] == gate), None)
    if selected is None or selected['kind'] != 'gate':
        raise InvalidPlan('gate reliance requires an existing gate ID')
    cap = selected.pop('max_age')
    selected['kind'] = 'merge'
    comparison = compare(plan, modified)
    counterfactual = audit(modified)
    old_checks = {row['node']: row for row in baseline['checks']}
    checks = []
    for row in counterfactual['checks']:
        old = old_checks[row['node']]
        passed = old['status'] == 'BOUND_WITHIN_BUDGET'
        still_passes = row['status'] == 'BOUND_WITHIN_BUDGET'
        checks.append({
            'node': row['node'], 'budget': row['budget'],
            'baseline_status': old['status'], 'without_gate_status': row['status'],
            'baseline_pass_lost': passed and not still_passes,
            'baseline': old, 'without_gate': row,
        })
    return {
        'schema': 'agebudget-gate-reliance-v1',
        'status': counterfactual['status'],
        'gate': gate, 'declared_cap': cap,
        'baseline': baseline, 'without_gate': counterfactual,
        'changes': comparison['changes'], 'checks': checks,
        'limitations': [
            'One selected gate becomes an immediate merge of its unchanged inputs.',
            'Every other gate, budget, tick unit and declared assumption stays fixed.',
            'A lost baseline pass concerns a declared bound, not an observed freshness violation.',
            'An unchanged pass may depend on another gate; this is not all-gates failure analysis.',
            'No availability, enforcement-failure probability or runtime bypass safety is established.',
        ],
    }
