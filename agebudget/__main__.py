"""Command-line entry point; emits JSON only after complete validation."""
import argparse
import json
import sys
from pathlib import Path

from . import InvalidPlan, audit, compare, loads

MAX_BYTES = 2 * 1024 * 1024


def main(argv=None):
    parser = argparse.ArgumentParser(description='Audit declared source-age bounds in a finite DAG')
    parser.add_argument('plan', help='UTF-8 JSON plan; no network access')
    parser.add_argument('--compare-to', metavar='PLAN', help='candidate plan using identical budgets and tick unit')
    args = parser.parse_args(argv)
    try:
        def read_plan(path):
            with Path(path).open('rb') as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise InvalidPlan('input exceeds 2 MiB')
            return loads(data.decode('utf-8'))

        plan = read_plan(args.plan)
        report = compare(plan, read_plan(args.compare_to)) if args.compare_to else audit(plan)
        payload = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n'
        sys.stdout.write(payload)
        sys.stdout.flush()
        return 0 if report['status'] == 'BOUND_WITHIN_BUDGET' else 1
    except (InvalidPlan, OSError, UnicodeError, RecursionError) as exc:
        print(f'agebudget: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
