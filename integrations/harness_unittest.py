"""Run unittest once; expose completed assertion failures as a repairable result.

This is an explicit opt-in helper, not a replacement for python -m unittest.
Loading errors, test exceptions, interruption and missing receipts remain errors.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def child(receipt, arguments):
    # A script normally puts its own directory first, unlike `python -m unittest`.
    # Keep project imports available without inheriting the bridge configuration.
    sys.path.insert(0, os.getcwd())
    loader = unittest.TestLoader()
    program = unittest.TestProgram(module=None, argv=['unittest'] + arguments,
                                  testLoader=loader, exit=False)
    result = program.result
    report = dict(tests_run=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped),
                  unexpected_successes=len(result.unexpectedSuccesses),
                  load_errors=len(loader.errors))
    Path(receipt).write_text(json.dumps(report), encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


def classify(report, exit_code):
    fields = {'tests_run', 'failures', 'errors', 'skipped',
              'unexpected_successes', 'load_errors'}
    if (not isinstance(report, dict) or set(report) != fields
            or any(type(v) is not int or v < 0 for v in report.values())):
        raise ValueError('Invalid unittest completion receipt')
    if exit_code not in (0, 1) or report['tests_run'] == 0 or report['load_errors'] or report['errors']:
        return 'test_execution_error', 2
    failed = bool(report['failures'] or report['unexpected_successes'])
    if exit_code != int(failed):
        raise ValueError('Unittest exit code does not match completion receipt')
    if not failed and report['skipped'] == report['tests_run']:
        return 'tests_skipped', 0
    return ('tests_failed' if failed else 'tests_passed'), 0


def main(arguments):
    # Only the parent can turn an assertion failure into a successful delivery of
    # a test result. A test calling sys.exit(0)/os._exit(0) cannot skip the receipt.
    with tempfile.TemporaryDirectory(prefix='harness-unittest-') as directory:
        receipt = Path(directory) / 'result.json'
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               '--bridge-child', str(receipt)] + arguments)
        try:
            report = json.loads(receipt.read_text(encoding='utf-8'))
            status, code = classify(report, proc.returncode)
        except (OSError, ValueError):
            print('bridge_unittest: no valid completed test result; stop and inspect the run.', file=sys.stderr)
            return 2
        print(json.dumps(dict(status=status, child_exit_code=proc.returncode, **report)), flush=True)
        if status == 'tests_failed':
            print('Assertions failed. Diagnose and fix the code; these tests did NOT pass.', flush=True)
        return code


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--bridge-child':
        sys.exit(child(sys.argv[2], sys.argv[3:]))
    sys.exit(main(sys.argv[1:]))
