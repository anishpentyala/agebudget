"""Compare model consequences under an unchanged set of age budgets."""
from .core import InvalidPlan, audit


def compare(before, after):
    """Audit two plans against identical targets and budgets, then compare bounds.

    Node IDs shared across plans must refer to the same logical component in the
    caller's model. The caller must use the same tick unit. Neither assumption
    can be checked from the plan format. This is not a safe-rollout decision.
    """
    old = audit(before)
    new = audit(after)
    old_budgets = {row['node']: row['budget'] for row in old['checks']}
    new_budgets = {row['node']: row['budget'] for row in new['checks']}
    if old_budgets != new_budgets:
        raise InvalidPlan('comparison requires identical budget targets and limits')
    old_nodes = {row['node']: row for row in old['nodes']}
    new_nodes = {row['node']: row for row in new['nodes']}
    changes = []
    for name in sorted(old_nodes.keys() | new_nodes.keys()):
        left, right = old_nodes.get(name), new_nodes.get(name)
        delta = None
        if left is None:
            change = 'ADDED'
        elif right is None:
            change = 'REMOVED'
        else:
            a, b = left['upper_bound'], right['upper_bound']
            if a is None and b is None:
                change = 'STILL_UNKNOWN'
            elif a is None:
                change = 'BOUND_ESTABLISHED'
            elif b is None:
                change = 'BOUND_LOST'
            else:
                delta = b - a
                change = 'BOUND_LOWER' if delta < 0 else (
                    'BOUND_HIGHER' if delta > 0 else 'BOUND_UNCHANGED')
        changes.append({'node': name, 'change': change, 'bound_delta': delta,
                        'before': left, 'after': right})
    old_checks = {row['node']: row for row in old['checks']}
    checks = []
    for right in new['checks']:
        left = old_checks[right['node']]
        checks.append({'node': right['node'], 'budget': right['budget'],
                       'before_status': left['status'], 'after_status': right['status'],
                       'before_excess': left['bound_excess'],
                       'after_excess': right['bound_excess']})
    return {'schema': 'agebudget-comparison-v1', 'status': new['status'],
            'before_status': old['status'], 'changes': changes, 'checks': checks,
            'limitations': [
                'Both plans must use the same tick unit and shared node identities.',
                'Lower declared bounds do not establish safer configuration or fresher observed data.',
                'Changed assumptions, unknowns and rejecting gates can alter bounds without improving runtime behavior.',
                'No availability, rollout safety or concrete execution-trace proof.',
            ]}
