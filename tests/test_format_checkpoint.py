import json
import tempfile
import unittest
from pathlib import Path

from server.context_rollover import checkpoint
from server.schemas import ChatCompletionRequest


class FormatCheckpointTests(unittest.TestCase):
    def test_history_is_archived_exactly_but_bad_markup_is_not_a_prompt_example(self):
        bad = '<｜DSML｜｜ calls>broken'
        req = ChatCompletionRequest(model='deepseek-expert', messages=[
            {'role': 'user', 'content': 'Investigate fixture, preserve files'},
            {'role': 'assistant', 'content': bad},
            {'role': 'assistant', 'tool_calls': [{'id': 'call1', 'type': 'function',
                'function': {'name': 'bash', 'arguments': '{"command":"pwd"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call1', 'content': 'Permission denied'}])
        with tempfile.TemporaryDirectory() as root:
            text, path = checkpoint(req, 'old:8', root, format_recovery=True)
            self.assertEqual(json.loads(Path(path).read_text())['request'], req.model_dump(exclude_none=True))
        self.assertNotIn(bad, text)
        self.assertIn('Permission denied', text)
        self.assertIn('manually reviewed format failure', text)
        self.assertNotIn('context refusal', text)
        self.assertNotIn('local reply cap', text)
        packet = json.loads(text.split('Checkpoint data:\n')[1])
        self.assertEqual(packet['recent_assistant_refs'], [1])
        self.assertEqual(packet['completed_tool_results'][0]['id'], 'call1')

    def test_conflicting_reasons_fail_before_archive(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                checkpoint(ChatCompletionRequest(messages=[]), 'old:8', root,
                           format_recovery=True, local_limit=True)
            self.assertEqual(list(Path(root).iterdir()), [])
