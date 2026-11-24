import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CLITests(unittest.TestCase):
    def run_plan(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'plan.json'
            path.write_bytes(data)
            return subprocess.run([sys.executable, '-m', 'agebudget', str(path)], capture_output=True)

    def test_invalid_no_stdout(self):
        for data in [b'{}', b'\xff', b'{"x":1,"x":2}', b' '* (2*1024*1024+1)]:
            with self.subTest(size=len(data)):
                result = self.run_plan(data)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(b'agebudget:', result.stderr)

    def test_examples(self):
        root = Path(__file__).resolve().parents[1]
        for name, code in [('layered.json', 1), ('gated.json', 0), ('unknown.json', 1)]:
            result = self.run_plan((root/'examples'/name).read_bytes())
            with self.subTest(name=name):
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)['schema'], 'agebudget-report-v1')

    def test_missing(self):
        result = subprocess.run([sys.executable, '-m', 'agebudget', '/does-not-exist/plan.json'], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
