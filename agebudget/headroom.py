"""One-variable declared residence headroom under fixed model assumptions."""
from copy import deepcopy

from .core import MAX_TICKS, InvalidPlan, audit


def residence_headroom(plan, node):
    """Find a hold's largest admitted residence preserving every declared budget.

    Only the selected finite residence changes. Requires the baseline audit to
    establish every budget. This is model arithmetic, not runtime tuning advice.
    """
    baseline = audit(plan)
    if type(node) is not str:
        raise InvalidPlan('headroom node must name a hold')
    work = deepcopy(plan)
    selected = next((row for row in work['nodes'] if row['id'] == node), None)
    if selected is None or selected['kind'] != 'hold':
        raise InvalidPlan('headroom node must name an existing hold')
    initial = selected['max_residence']
    if initial is None:
        raise InvalidPlan('headroom requires a finite selected residence')
    if baseline['status'] != 'BOUND_WITHIN_BUDGET':
        raise InvalidPlan('headroom requires every baseline budget to be within its bound')

    def evaluate(value):
        selected['max_residence'] = value
        return audit(work)

    # Bounds are monotone in this single nonnegative scalar: addition, maximum
    # and minimum with fixed gates all preserve order. At most50 bisections in
    # the admitted integer domain, independent of the numeric headroom size.
    low, high = initial, MAX_TICKS
    while low < high:
        middle = (low + high + 1) // 2
        if evaluate(middle)['status'] == 'BOUND_WITHIN_BUDGET':
            low = middle
        else:
            high = middle - 1
    at_limit = evaluate(low)
    next_audit = evaluate(low + 1) if low < MAX_TICKS else None
    return {
        'schema': 'agebudget-residence-headroom-v1',
        'status': at_limit['status'],
        'node': node,
        'initial_residence': initial,
        'max_residence': low,
        'additional_residence': low - initial,
        'domain_limited': low == MAX_TICKS,
        'baseline': baseline,
        'at_limit': at_limit,
        'next_tick': next_audit,
        'limitations': [
            'Only the selected hold residence varies; every other assumption and budget is fixed.',
            'Largest admitted integer residence, not a runtime TTL recommendation or rollout safety proof.',
            'Gate shielding can make headroom reach the input domain limit while rejecting every value.',
            'An unknown baseline budget or unknown selected residence is refused, not treated as zero.',
            'Reaching the domain limit does not establish unlimited headroom.',
        ],
    }
