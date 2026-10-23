import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from integrations.harness_unittest import classify

HELPER = Path(__file__).resolve().parents[1] / 'integrations/harness_unittest.py'


class UnittestRunnerTests(unittest.TestCase):
    def run_test(self, source, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'test_sample.py').write_text(source)
            return subprocess.run([sys.executable, str(HELPER)] + list(arguments or ('test_sample', '-v')),
                                  cwd=directory, capture_output=True, text=True, timeout=10)

    def test_pass_and_assertion_failures_are_explicit_results(self):
        for expression, status, child_code in [('True', 'tests_passed', 0), ('False', 'tests_failed', 1)]:
            with self.subTest(status=status):
                result = self.run_test('import unittest\nclass Test(unittest.TestCase):\n def test_it(self): self.assertTrue(' + expression + ')\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout.splitlines()[0])
                self.assertEqual(report['status'], status)
                self.assertEqual(report['child_exit_code'], child_code)
                if child_code:
                    self.assertIn('FAILED (failures=1)', result.stderr)
                    self.assertIn('did NOT pass', result.stdout)

    def test_execution_errors_never_become_repairable_assertions(self):
        cases = [
            'raise ImportError("missing dependency")',
            'invalid python !!!',
            'import os; os._exit(0)',
            'import sys; sys.exit(0)',
            'raise KeyboardInterrupt',
            'import unittest\nclass Test(unittest.TestCase):\n def test_it(self): raise PermissionError("denied")',
            'import unittest\nclass Test(unittest.TestCase):\n def test_it(self): raise RuntimeError("uncertain operation")',
            'import unittest\nclass Test(unittest.TestCase):\n @classmethod\n def tearDownClass(cls): raise RuntimeError("cleanup failed")\n def test_it(self): self.fail("assertion")',
            '',
        ]
        for source in cases:
            with self.subTest(source=source):
                result = self.run_test(source)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('"status": "tests_failed"', result.stdout)

    def test_discovery_and_missing_module(self):
        result = self.run_test('import unittest\nclass Test(unittest.TestCase):\n def test_it(self): pass\n', 'discover', '-s', '.', '-v')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"tests_run": 1', result.stdout)
        self.assertNotEqual(self.run_test('', 'missing_module').returncode, 0)

    def test_skipped_suite_is_not_reported_as_passed(self):
        result = self.run_test('import unittest\nclass Test(unittest.TestCase):\n @unittest.skip("unavailable")\n def test_it(self): pass\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "tests_skipped"', result.stdout)
        self.assertNotIn('tests_passed', result.stdout)

    def test_invalid_and_inconsistent_receipts_are_rejected(self):
        valid = dict(tests_run=1, failures=1, errors=0, skipped=0, unexpected_successes=0, load_errors=0)
        for report, code in [(None, 1), ({}, 1), ({**valid, 'failures': True}, 1), (valid, 0)]:
            with self.subTest(report=report), self.assertRaises(ValueError):
                classify(report, code)
        self.assertEqual(classify(valid, -15), ('test_execution_error', 2))
