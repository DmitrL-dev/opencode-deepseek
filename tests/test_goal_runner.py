"""ds-goal resume policy against a fake headless Harness; no bridge or account."""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations import goal_runner

FORMAT = 'The single format correction also failed; no further correction was requested. ' \
         'Invalid or incomplete DSML tool envelope; no calls were returned'

FAKE = r'''import json, os, sys
from pathlib import Path
state = Path(os.environ['FAKE_STATE'])
calls = json.loads(state.read_text()) if state.exists() else []
calls.append({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'dsh_home': os.environ.get('DSH_HOME'),
              'overlay': Path(sys.argv[sys.argv.index('--patch') + 1]).read_text()})
state.write_text(json.dumps(calls))
with open(os.environ['FAKE_BRIDGE_LOG'], 'a') as log:
    log.write('[toolcalls] format_correction=requested\n')
end = json.loads(os.environ['FAKE_ENDS'])[len(calls) - 1]
print(json.dumps({'type': 'session', 'sessionId': 'session-fake'}), flush=True)
if os.environ.get('FAKE_BACKGROUND'):
    import subprocess
    subprocess.Popen(['/bin/sh', '-c', 'sleep 2; echo late > ' + os.environ['FAKE_LATE']])
if end['kind'] == 'hang':
    # A tool command that would keep changing the project after its parent died.
    import subprocess, time
    subprocess.Popen(['/bin/sh', '-c', 'sleep 2; echo late > ' + os.environ['FAKE_LATE']])
    time.sleep(30)
print(json.dumps({'type': 'tool_call', 'tool': 'read'}))
print(json.dumps({'type': 'tool_result', 'status': 'completed'}))
print(json.dumps({'type': 'status', 'phase': 'step_end'}))
print(json.dumps({'type': 'status', 'phase': 'turn_end', 'reason': end}))
print(json.dumps({'type': 'final', 'text': 'done' if end['kind'] == 'completed' else ''}))
sys.exit(0 if end['kind'] == 'completed' else 1)
'''


def failed(message):
    return {'kind': 'error', 'error': {'message': message, 'code': 'PI_AI_ERROR'}}


class GoalRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.dsh = self.root / 'dsh'
        self.dsh.write_text('#!' + sys.executable + '\n' + FAKE)
        self.dsh.chmod(self.dsh.stat().st_mode | stat.S_IXUSR)
        self.state = self.root / 'calls.json'
        self.bridge_log = self.root / 'bridge.log'
        self.bridge_log.write_text('old [toolcalls] format_correction=invalid\n')
        overlay = patch.object(goal_runner, 'build_overlay', return_value=b'- id: SECRET-BRIDGE-TOKEN\n')
        overlay.start()
        self.addCleanup(overlay.stop)

    def run_goal(self, ends, max_resumes=2, **kwargs):
        with patch.dict(os.environ, {'FAKE_STATE': str(self.state), 'FAKE_ENDS': json.dumps(ends),
                                     'FAKE_BRIDGE_LOG': str(self.bridge_log),
                                     'FAKE_LATE': str(self.work / 'late.txt')}):
            return goal_runner.run('Implement SPEC.md', self.work, dsh=str(self.dsh),
                                   max_resumes=max_resumes, base_url='http://127.0.0.1:1/v1',
                                   model='deepseek-expert', runs_root=self.root / 'runs',
                                   logs_dir=self.root / 'logs', bridge_log=self.bridge_log, **kwargs)

    def calls(self):
        return json.loads(self.state.read_text())

    def test_completed_run_once_with_test_rule_in_isolated_home(self):
        result = self.run_goal([{'kind': 'completed'}])
        self.assertEqual((result['status'], result['resumes']), ('completed', 0))
        (call,) = self.calls()
        self.assertEqual(Path(call['cwd']).resolve(), self.work.resolve())
        self.assertTrue(call['dsh_home'].startswith(str((self.root / 'runs').resolve())))
        self.assertIn('bridge_unittest python3 discover -s tests', call['argv'][-1])
        self.assertNotIn('-m unittest`', goal_runner.TEST_RULE.split('never add')[0])
        self.assertNotIn('--session-id', call['argv'])
        self.assertEqual(result['format_corrections'], {'requested': 1})

    def test_model_format_stop_resumes_same_session_up_to_the_limit(self):
        result = self.run_goal([failed(FORMAT), failed(FORMAT), failed(FORMAT), {'kind': 'completed'}])
        self.assertEqual((result['status'], result['resumes']), ('model_format', 2))
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        for call in calls[1:]:
            self.assertEqual(call['argv'][call['argv'].index('--session-id') + 1], 'session-fake')
            self.assertIn('nothing from it ran', call['argv'][-1])
        self.assertEqual(result['format_corrections'], {'requested': 3})

    def test_shell_abort_and_bridge_refusals_are_never_resumed(self):
        for end in ({'kind': 'aborted', 'reason': {'kind': 'user'}},
                    failed('History is unknown; automatic replay is disabled'),
                    failed('Local request budget exhausted')):
            with self.subTest(end=end):
                self.state.unlink(missing_ok=True)
                result = self.run_goal([end, {'kind': 'completed'}])
                self.assertEqual(result['resumes'], 0)
                self.assertEqual(len(self.calls()), 1)
                self.assertNotEqual(result['status'], 'completed')

    def test_zero_resumes_stops_at_first_model_failure(self):
        result = self.run_goal([failed(FORMAT), {'kind': 'completed'}], max_resumes=0)
        self.assertEqual((result['status'], len(self.calls())), ('model_format', 1))

    def test_token_overlay_is_removed_and_continue_reuses_the_run(self):
        first = self.run_goal([failed('Local request budget exhausted')])
        run_dir = Path(first['run_dir'])
        self.assertFalse((run_dir / 'bridge.patch.yml').exists())
        self.assertNotIn('SECRET-BRIDGE-TOKEN', json.dumps(first))
        self.assertEqual(oct(run_dir.stat().st_mode & 0o777), '0o700')
        self.state.unlink()
        relative = Path(os.path.relpath(run_dir))
        second = self.run_goal([{'kind': 'completed'}], run_dir=relative, session=first['session'])
        self.assertEqual(second['status'], 'completed')
        (call,) = self.calls()
        self.assertIn('--session-id', call['argv'])
        self.assertTrue((run_dir / 'events-2.jsonl').exists())
        self.assertFalse((run_dir / 'bridge.patch.yml').exists())

    def test_timeout_stops_every_process_started_by_harness(self):
        result = self.run_goal([{'kind': 'hang'}], attempt_timeout=2)
        self.assertEqual((result['status'], result['resumes']), ('timeout', 0))
        time.sleep(3)
        self.assertFalse((self.work / 'late.txt').exists())

    def test_interrupt_stops_the_tree_and_keeps_a_continuable_summary(self):
        real_wait = goal_runner._wait

        def interrupted(proc, timeout):
            time.sleep(1)
            raise KeyboardInterrupt
        with patch.object(goal_runner, '_wait', side_effect=interrupted):
            result = self.run_goal([{'kind': 'hang'}])
        self.assertIs(goal_runner._wait, real_wait)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['session'], 'session-fake')
        summary = json.loads((Path(result['run_dir']) / 'summary.json').read_text())
        self.assertEqual(summary['session'], 'session-fake')
        self.assertFalse((Path(result['run_dir']) / 'bridge.patch.yml').exists())
        time.sleep(3)
        self.assertFalse((self.work / 'late.txt').exists())

    def test_continue_with_a_broken_summary_is_a_clean_error(self):
        run_dir = self.root / 'runs' / 'broken'
        run_dir.mkdir(parents=True)
        for content in ('{"session": "session-fake"}', 'not json', '[]'):
            (run_dir / 'summary.json').write_text(content)
            with self.subTest(content=content), self.assertRaises(SystemExit) as stop, \
                    patch('sys.stderr'):
                goal_runner.main(['--continue', str(run_dir), '--dsh', str(self.dsh)])
            self.assertEqual(stop.exception.code, 2)
        self.assertFalse(self.state.exists())

    def test_background_commands_do_not_outlive_a_finished_run(self):
        os.environ['FAKE_BACKGROUND'] = '1'
        self.addCleanup(os.environ.pop, 'FAKE_BACKGROUND')
        result = self.run_goal([{'kind': 'completed'}])
        self.assertEqual(result['status'], 'completed')
        time.sleep(3)
        self.assertFalse((self.work / 'late.txt').exists())

    def test_test_rule_applies_only_to_python_unittest_projects(self):
        self.assertIn('Python unittest', goal_runner.TEST_RULE)
        self.assertIn('other test runners', goal_runner.TEST_RULE)


if __name__ == '__main__':
    unittest.main()
