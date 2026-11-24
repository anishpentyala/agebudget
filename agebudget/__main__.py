"""Command-line entry point with checked input and output delivery."""
import argparse
import json
import os
import stat
import sys

from . import InvalidPlan, audit, compare, loads, residence_headroom

MAX_BYTES = 2 * 1024 * 1024


def _deliver(text, stream):
    written = stream.write(text)
    if written != len(text):
        raise OSError('short output write')
    stream.flush()


class _Parser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            _deliver(message, file if file is not None else sys.stderr)


def _read_plan(path):
    # Nonblocking open prevents a FIFO from waiting for a writer before fstat.
    # Inspect the opened descriptor, not a separate path check with a race.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise InvalidPlan('plan input must be a regular file')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            data = stream.read(MAX_BYTES + 1)
    finally:
        if fd is not None:
            os.close(fd)
    if len(data) > MAX_BYTES:
        raise InvalidPlan('input exceeds 2 MiB')
    return loads(data.decode('utf-8'))


def _run(argv):
    try:
        parser = _Parser(description='Audit declared source-age bounds in a finite DAG')
        parser.add_argument('plan', help='UTF-8 JSON regular file; no network access')
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument('--headroom', metavar='HOLD', help='largest admitted residence for one hold under fixed budgets')
        modes.add_argument('--compare-to', metavar='PLAN', help='candidate plan using identical budgets and tick unit')
        args = parser.parse_args(argv)
        plan = _read_plan(args.plan)
        if args.headroom is not None:
            report = residence_headroom(plan, args.headroom)
        else:
            report = compare(plan, _read_plan(args.compare_to)) if args.compare_to else audit(plan)
        payload = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n'
        _deliver(payload, sys.stdout)
        return (0 if report['status'] == 'BOUND_WITHIN_BUDGET' else 1), False
    except SystemExit as exc:
        # argparse help/usage has already used the checked output path.
        return int(exc.code), False
    except (InvalidPlan, OSError, UnicodeError, RecursionError, ValueError) as exc:
        try:
            _deliver(f'agebudget: {exc}\n', sys.stderr)
        except (OSError, ValueError):
            pass
        return 2, True


def main(argv=None):
    """Return an exit status without replacing or closing caller-owned streams."""
    return _run(argv)[0]


def entrypoint():
    """Prevent a failed buffered stdout flush being retried at process shutdown."""
    code, failed = _run(None)
    if failed:
        try:
            fd = sys.stdout.fileno()
            sink = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(sink, fd)
            finally:
                if sink != fd:
                    os.close(sink)
        except (OSError, ValueError, AttributeError):
            sys.stdout = open(os.devnull, 'w')
    return code


if __name__ == '__main__':
    raise SystemExit(entrypoint())
