"""Completion acceptance is separate from model assertions and tool success."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


class GoalCompletionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_partial_scope_and_review_lifecycle(self):
        result = subprocess.run(['node', str(Path(__file__).with_name('harness_goal_completion_fixture.mjs'))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.getenv('RUN_HARNESS_FIXTURES') == '1', 'opt-in installed Harness fixture')
class GoalCompletionRuntimeTests(unittest.TestCase):
    def test_partial_result_cannot_close_native_goal_or_trigger_retry(self):
        from tests.test_harness_runtime import HarnessRuntimeTests
        from tests.test_tools import block
        from providers import access
        from deepseek.client import Reply
        case = HarnessRuntimeTests('test_text_uses_one_streamed_request')
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.install_shell_safety_fixture()

        def chat(prompt, *args):
            attempt = access.current_attempt('deepseek')
            attempt.dispatch()
            case.prompts.append(prompt)
            step = len(case.prompts)
            self.assertLessEqual(step, 2, 'No continuation or replay after completion denial')
            if step == 1:
                answer = block([{'name': 'create_goal', 'arguments': {
                    'objective': 'Implement ALL plans including Windows and Linux acceptance.'}}])
            else:
                messages = case.calls[-1]['messages']
                native = [m for m in messages if m['role'] == 'tool'][-1]
                value = json.loads(native['content'])
                goal = value.get('goal', value)
                answer = 'One component is done. Full platform acceptance is outside this report.\n' + block([
                    {'name': 'update_goal', 'arguments': {'goal_id': goal['id'], 'revision': goal['revision'], 'action': 'complete'}}])
            attempt.complete()
            return Reply(answer, f'owned-fixture:{step * 2}')

        case.chat = chat
        result = case.run_harness('Create a goal to implement ALL plans including Windows and Linux acceptance, then work on it.')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(case.calls), 2)
        self.assertIn('GOAL_COMPLETION_REVIEW_REQUIRED', result.stdout)
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(any(r.get('phase') == 'turn_end' and r['reason']['kind'] == 'aborted' for r in records))
        sessions = list((case.root / 'dsh' / 'sessions').rglob('session.v4.jsonl*'))
        self.assertEqual(len(sessions), 1)
        decoded = subprocess.run(['node', '--input-type=module', '-e', '''
import {readFileSync} from 'node:fs';
import {zstdDecompressSync} from 'node:zlib';
const data = readFileSync(process.argv[1]);
if (!process.argv[1].endsWith('.zstd')) process.stdout.write(data);
else {
  const magic = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
  let start = 0;
  while (start < data.length) {
    const found = data.indexOf(magic, start + 4);
    const end = found < 0 ? data.length : found;
    process.stdout.write(zstdDecompressSync(data.subarray(start, end)));
    start = end;
  }
}
''', str(sessions[0])], capture_output=True, text=True, timeout=10, check=True)
        events = [json.loads(line) for line in decoded.stdout.splitlines()]
        changes = [e for e in events if e.get('type') == 'goal/change']
        self.assertTrue(changes, str(events)[:1500])
        self.assertNotIn('"operation": "complete"', json.dumps(changes))
