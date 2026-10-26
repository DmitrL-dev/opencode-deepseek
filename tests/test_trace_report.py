"""Offline outcome counts from TOOLCALL_LOG traces; never prints trace content."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import toolcall_logging
from server.trace_report import classify, report

SECRET = 'PRIVATE-ARGUMENT-VALUE'


def frame(delta=None, finish=None):
    return 'data: ' + json.dumps({'object': 'chat.completion.chunk', 'choices': [
        {'index': 0, 'delta': delta or {}, 'finish_reason': finish}]})


def error(message, err_type='invalid_tool_response'):
    return json.dumps({'error': {'message': message, 'type': err_type, 'code': err_type}})


class TraceReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = patch.dict(os.environ, {'TOOLCALL_LOG': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def trace(self, name, *responses):
        path = self.root / name
        path.touch()
        for frames in responses:
            toolcall_logging.record(path, 'from_agent', {'messages': [{'content': SECRET}]})
            toolcall_logging.record(path, 'from_provider', SECRET)
            for item in frames:
                toolcall_logging.record(path, 'to_agent', item)
        return path

    def test_streamed_frames_are_one_response(self):
        call = {'tool_calls': [{'index': 0, 'function': {'name': 'read', 'arguments': SECRET}}]}
        self.trace('1023100000-deepseek.log',
                   [frame({'role': 'assistant'}), frame(call), frame(finish='tool_calls'), 'data: [DONE]'],
                   [frame({'content': SECRET}), frame(finish='stop'), 'data: [DONE]'])
        result = report(self.root)
        self.assertEqual(result['outcomes'], {'calls': 1, 'text': 1})
        self.assertEqual(result['model_step_success'], 1.0)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_errors_split_model_failures_from_bridge_and_request_refusals(self):
        self.trace('1024100000-deepseek.log',
                   ['data: ' + error('Invalid or incomplete DSML tool envelope; no calls were returned')],
                   [error('Tool arguments do not satisfy the advertised schema; ' + SECRET)],
                   [error('The provider reached its output limit before completing the tool reply')],
                   [error('History is unknown; automatic replay is disabled')],
                   [error('`messages` must not be empty', 'invalid_request_error')],
                   [frame(finish='tool_calls')])
        result = report(self.root)
        self.assertEqual(result['outcomes'], {
            'bridge_state': 1, 'calls': 1, 'model_format': 1, 'model_incomplete': 1,
            'model_schema': 1, 'request_refused': 1})
        self.assertEqual(result['model_steps'], 4)
        self.assertEqual(result['model_step_success'], 0.25)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_days_come_from_trace_names(self):
        self.trace('1023100000-deepseek.log', [frame(finish='tool_calls')])
        self.trace('1024100000-deepseek-1.log', [error('Unsupported tool-call markup; no calls were returned')])
        days = report(self.root)['by_day']
        self.assertEqual(days['1023']['model_step_success'], 1.0)
        self.assertEqual(days['1024']['model_step_success'], 0.0)

    def test_traces_without_agent_replies_and_garbage_are_not_steps(self):
        (self.root / '1023100000-deepseek.log').write_text('not a trace')
        path = self.root / '1023100001-deepseek.log'
        path.touch()
        toolcall_logging.record(path, 'search_chat', SECRET)
        self.assertEqual(report(self.root)['model_steps'], 0)
        self.assertIsNone(report(self.root)['model_step_success'])
        self.assertEqual(classify('data: {broken'), 'unknown')


if __name__ == '__main__':
    unittest.main()
