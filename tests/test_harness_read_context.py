"""Runtime-only read reminders; installed Harness checks use synthetic replies."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest


class ReadContextTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_context_has_no_execution_or_history_side_effects(self):
        result = subprocess.run(['node', str(Path(__file__).with_name('harness_read_context_fixture.mjs'))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.getenv('RUN_HARNESS_FIXTURES') == '1', 'opt-in installed Harness fixture')
class ReadContextRuntimeTests(unittest.TestCase):
    def exercise(self, mode):
        from tests.test_harness_runtime import HarnessRuntimeTests
        from tests.test_tools import block
        from providers import access
        from deepseek.client import Reply
        case = HarnessRuntimeTests('test_text_uses_one_streamed_request')
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.install_shell_safety_fixture()
        file = case.workspace / 'fixture.txt'
        old = file.read_text()
        reads, epochs = [], []

        def chat(prompt, *args):
            attempt = access.current_attempt('deepseek')
            attempt.dispatch()
            case.prompts.append(prompt)
            step = len(case.prompts)
            request = case.calls[-1]
            # The notice is a native runtime context message, never a changed
            # system instruction or a synthetic tool result.
            self.assertNotIn('Filesystem observation runtime:', json.dumps(
                [m for m in request['messages'] if m['role'] == 'system']))
            user_context = json.dumps([m for m in request['messages'] if m['role'] == 'user'])
            epoch = re.findall(r'Filesystem observation runtime: ([0-9a-f-]+)', user_context)[-1]
            epochs.append(epoch)
            if step == 1 or step == 3 and mode != 'missing-read':
                reads.append(step)
                answer = block([{'name': 'read', 'arguments': {'file_path': str(file)}}])
            elif step == 2:
                answer = 'FIRST_READ_COMPLETE'
            elif step == 4 or step == 3 and mode == 'missing-read':
                if mode == 'stale':
                    file.write_text('EXTERNAL_CHANGE\n')
                answer = block([{'name': 'edit', 'arguments': {'file_path': str(file),
                    'old_string': old.strip(), 'new_string': 'READ_RESTORED_EDIT_OK'}}])
            else:
                self.assertEqual(mode, 'success', 'No request allowed after a tool error')
                answer = 'RESTORED_READ_COMPLETE'
            attempt.complete()
            return Reply(answer, f'owned-fixture:{step * 2}')

        case.chat = chat
        first = case.run_harness('Read the owned fixture, then finish.')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(len(case.calls), 2)
        sessions = list((case.root / 'dsh' / 'sessions').rglob('session.v4.jsonl*'))
        self.assertEqual(len(sessions), 1)
        history = sessions[0].read_bytes()
        session_id = sessions[0].parent.name
        resumed = case.run_harness('Continue the same task after restart; preserve completed work.',
                                   app_args=['--session-id', session_id])
        self.assertEqual(resumed.returncode, 0 if mode == 'success' else 1,
                         resumed.stdout + resumed.stderr)
        self.assertTrue(sessions[0].read_bytes().startswith(history), 'Old history was rewritten')
        self.assertEqual(epochs[0], epochs[1])
        self.assertNotEqual(epochs[1], epochs[2])
        self.assertTrue(all(epoch == epochs[2] for epoch in epochs[2:]))
        if mode == 'success':
            self.assertEqual(len(case.calls), 5)
            self.assertEqual(reads, [1, 3])
            self.assertEqual(file.read_text(), 'READ_RESTORED_EDIT_OK\n')
            self.assertIn('RESTORED_READ_COMPLETE', resumed.stdout)
        elif mode == 'missing-read':
            self.assertEqual(len(case.calls), 3)
            self.assertEqual(file.read_text(), old)
            self.assertIn('file has not been read', resumed.stdout)
        else:
            self.assertEqual(len(case.calls), 4)
            self.assertEqual(file.read_text(), 'EXTERNAL_CHANGE\n')
            self.assertIn('file changed since it was read', resumed.stdout)
        if mode != 'success':
            records = [json.loads(line) for line in resumed.stdout.splitlines() if line.startswith('{')]
            errors = [r for r in records if r['type'] == 'tool_result' and r['status'] == 'error']
            self.assertEqual(len(errors), 1)
            self.assertTrue(any(r.get('phase') == 'turn_end' and
                                r['reason']['kind'] == 'aborted' for r in records))

    def test_restart_real_read_then_edit_preserves_history(self):
        self.exercise('success')

    def test_old_read_cannot_authorize_edit_after_restart(self):
        self.exercise('missing-read')

    def test_change_after_fresh_read_still_blocks_edit(self):
        self.exercise('stale')
