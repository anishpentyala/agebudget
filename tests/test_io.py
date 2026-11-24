import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agebudget.__main__ import main


class Output(io.StringIO):
    def __init__(self, behavior):
        super().__init__()
        self.behavior = behavior

    def write(self, text):
        if self.behavior == 'short':
            super().write(text[:3])
            return 3
        if self.behavior == 'none':
            return None
        if self.behavior == 'closed':
            raise ValueError('closed stream')
        if self.behavior == 'broken':
            raise BrokenPipeError('pipe closed')
        return super().write(text)

    def flush(self):
        if self.behavior == 'flush':
            raise OSError('flush failed')
        return super().flush()


class IOReliability(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'plan.json'
        self.path.write_text(json.dumps({'schema': 'agebudget-plan-v1',
            'nodes': [{'id': 's', 'kind': 'source', 'max_age': 1}],
            'budgets': [{'node': 's', 'max_age': 2}]}), encoding='utf8')

    def test_output_failures_return_error_without_closing_caller_stream(self):
        for behavior in ['short', 'none', 'closed', 'broken', 'flush']:
            for args in [[str(self.path)], [str(self.path), '--compare-to', str(self.path)], ['--help']]:
                with self.subTest(behavior=behavior, args=args):
                    stream = Output(behavior)
                    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(args), 2)
                        self.assertIs(sys.stdout, stream)
                    self.assertFalse(stream.closed)

    def test_failed_stderr_is_best_effort(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(Output('broken')):
            self.assertEqual(main([str(self.path)+'-missing']), 2)

    def test_success_leaves_caller_streams_unchanged(self):
        stream = Output('ok')
        with contextlib.redirect_stdout(stream):
            self.assertEqual(main([str(self.path)]), 0)
            self.assertIs(sys.stdout, stream)
        self.assertFalse(stream.closed)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'BOUND_WITHIN_BUDGET')

    def test_help_and_usage(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--help']), 0)
            self.assertIn('--compare-to', out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([]), 2)
            self.assertIn('usage:', err.getvalue())

    def test_nonregular_input_is_rejected(self):
        candidates = [Path(self.directory.name)]
        if hasattr(os, 'mkfifo'):
            fifo = Path(self.directory.name)/'fifo'
            os.mkfifo(fifo)
            candidates.append(fifo)
        if os.path.exists(os.devnull):
            candidates.append(Path(os.devnull))
        for path in candidates:
            for args in [[str(path)], [str(self.path), '--compare-to', str(path)]]:
                with self.subTest(path=str(path), args=args):
                    proc = subprocess.run([sys.executable, '-m', 'agebudget', *args],
                                          capture_output=True, timeout=3)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(proc.stdout, b'')

    def test_descriptor_is_closed_when_validation_fails(self):
        from agebudget.__main__ import _read_plan
        captured = []
        real_open = os.open

        def record_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            captured.append(fd)
            return fd

        with mock.patch('agebudget.__main__.os.open', side_effect=record_open):
            with mock.patch('agebudget.__main__.os.fstat', side_effect=OSError('stat failed')):
                with self.assertRaises(OSError):
                    _read_plan(str(self.path))
        self.assertEqual(len(captured), 1)
        with self.assertRaises(OSError):
            os.fstat(captured[0])

    def test_regular_symlink_is_allowed(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('symlinks unavailable')
        link = Path(self.directory.name)/'link'
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest('symlink creation not permitted')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(link)]), 0)

    @unittest.skipUnless(os.path.exists('/dev/full'), 'requires Linux /dev/full')
    def test_process_wrapper_preserves_error_exit_on_shutdown(self):
        for args in [[str(self.path)], ['--help']]:
            with open('/dev/full', 'wb') as sink:
                proc = subprocess.run([sys.executable, '-m', 'agebudget', *args],
                                      stdout=sink, stderr=subprocess.PIPE)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertNotIn(b'Exception ignored', proc.stderr)
            if args == ['--help']:
                continue
            with open('/dev/full', 'wb') as sink:
                proc = subprocess.run(['agebudget', *args], stdout=sink, stderr=subprocess.PIPE) if os.environ.get('AGEBUDGET_TEST_CONSOLE') else None
            if proc is not None:
                self.assertEqual(proc.returncode, 2, proc.stderr)
