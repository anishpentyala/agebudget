"""Checked delivery contracts for embedded and process-owned calls."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agebudget.__main__ import entrypoint, main


class OddWrite(io.StringIO):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def write(self, text):
        super().write(text)
        return self.result(len(text))


class LateFlush(io.StringIO):
    def __init__(self, allowed=0):
        super().__init__()
        self.allowed = allowed
        self.calls = 0

    def flush(self):
        self.calls += 1
        if self.calls > self.allowed:
            raise OSError('synthetic shutdown failure')
        return super().flush()


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plan = Path(self.temp.name) / 'plan.json'
        self.plan.write_text(json.dumps({
            'schema': 'agebudget-plan-v1',
            'nodes': [{'id': 's', 'kind': 'source', 'max_age': 1}],
            'budgets': [{'node': 's', 'max_age': 2}]}))

    def modes(self):
        return [[str(self.plan)], [str(self.plan), '--compare-to', str(self.plan)], ['--help'], []]

    def test_noninteger_equal_length_is_rejected(self):
        class IntegerSubclass(int):
            pass
        for result in (float, IntegerSubclass):
            for args in self.modes():
                with self.subTest(result=result.__name__, args=args):
                    stream = OddWrite(result)
                    with patch('sys.stdout', stream), patch('sys.stderr', stream):
                        self.assertEqual(main(args), 2)
                        self.assertIs(sys.stdout, stream)
                    self.assertFalse(stream.closed)

    def test_unavailable_streams_are_errors(self):
        for args in self.modes():
            with self.subTest(args=args), patch('sys.stdout', None), patch('sys.stderr', None):
                self.assertEqual(main(args), 2)

    def test_shutdown_checks_both_streams_even_after_help(self):
        for args in ([str(self.plan)], ['--help'], []):
            for name in ('stdout', 'stderr'):
                with self.subTest(args=args, name=name):
                    out, err = io.StringIO(), io.StringIO()
                    bad = LateFlush()
                    with patch('sys.argv', ['agebudget', *args]), patch('sys.stdout', out), patch('sys.stderr', err):
                        setattr(sys, name, bad)
                        self.assertEqual(entrypoint(), 2)
                        self.assertIsNone(getattr(sys, name))

    def test_failure_only_on_final_flush_is_error(self):
        for args in ([str(self.plan)], ['--help']):
            stream = LateFlush(allowed=1)
            with self.subTest(args=args), patch('sys.argv', ['agebudget', *args]), patch('sys.stdout', stream), patch('sys.stderr', io.StringIO()):
                self.assertEqual(entrypoint(), 2)
                self.assertIsNone(sys.stdout)
                self.assertEqual(stream.calls, 2)

    def test_none_write_and_short_write_still_fail(self):
        for result in (lambda n: None, lambda n: n - 1, lambda n: True):
            stream = OddWrite(result)
            with self.subTest(result=result), patch('sys.stdout', stream), patch('sys.stderr', io.StringIO()):
                self.assertEqual(main([str(self.plan)]), 2)

    def test_successful_wrappers_preserve_streams(self):
        for args, code in (([str(self.plan)], 0), (['--help'], 0), ([], 2)):
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.argv', ['agebudget', *args]), patch('sys.stdout', out), patch('sys.stderr', err):
                self.assertEqual(entrypoint(), code)
                self.assertIs(sys.stdout, out)
                self.assertIs(sys.stderr, err)

    @unittest.skipUnless(os.path.exists('/dev/full'), 'Linux full device required')
    def test_real_help_and_usage_full_device(self):
        for args, destination in (([str(self.plan)], 'stdout'), (['--help'], 'stdout'), ([], 'stderr')):
            with self.subTest(args=args), open('/dev/full', 'wb') as sink:
                command = ['agebudget'] if os.environ.get('AGEBUDGET_TEST_CONSOLE') else [sys.executable, '-m', 'agebudget']
                result = subprocess.run([*command, *args],
                    stdout=sink if destination == 'stdout' else subprocess.PIPE,
                    stderr=sink if destination == 'stderr' else subprocess.PIPE,
                    timeout=5)
                self.assertEqual(result.returncode, 2)
                if destination != 'stderr':
                    self.assertNotIn(b'Exception ignored', result.stderr)
