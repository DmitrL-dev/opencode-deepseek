"""A corpus verdict must name the wire policy it actually verifies."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server.offline_corpus import replay


TOOLS = [{'function': {'name': 'fixture.save', 'parameters': {
    'type': 'object', 'properties': {'target': {'type': 'string'}},
    'required': ['target'], 'additionalProperties': False}}}]
PACKET = ('<｜DSML｜ calls>\n<｜DSML｜ invoke name="fixture.save">\n'
          '<｜DSML｜ parameter name="target" string="true">/fixture/value'
          '</｜DSML｜ parameter>\n</DSML｜ invoke>\n</DSML｜ calls>')
CALLS = {'kind': 'calls', 'calls': [
    {'name': 'fixture.save', 'arguments': {'target': '/fixture/value'}}]}


class OfflineCorpusPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_case(self, *, reply=PACKET, expected=CALLS, data=None, **policy):
        source = self.root / 'reply.json'
        source.write_text(json.dumps(data if data is not None else {
            'reply': reply, 'tools': TOOLS, 'finish_reason': 'stop'}))
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps([{
            'file': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'provider': 'deepseek', 'expected': expected, **policy}]))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch('socket.socket', side_effect=AssertionError('No account access')):
            result = replay(manifest)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertNotIn('/fixture/value', json.dumps(result))
        return result

    def test_explicit_dsml_policy_matches_each_reply_contract(self):
        for implementation in ('legacy',):
            with self.subTest(implementation=implementation):
                result = self.run_case(protocol='dsml', implementation=implementation)
                self.assertEqual(result['failed_indices'], [])
                self.assertEqual(result['matched_outcomes'], {'calls': 1, 'text': 0, 'reject': 0})

    def test_default_is_unchanged_and_never_inferred_from_the_reply_or_environment(self):
        with patch.dict('os.environ', {'DEEPSEEK_TOOL_PROTOCOL': 'dsml'}):
            result = self.run_case(expected={'kind': 'reject'})
        self.assertEqual(result['failed_indices'], [])
        self.assertEqual(result['matched_outcomes'], {'calls': 0, 'text': 0, 'reject': 1})

    def test_invalid_policy_is_a_failed_check_not_an_expected_refusal(self):
        for policy in ({'protocol': 'automatic'}, {'implementation': 'guess'},
                       {'protocol': True}, {'implementation': None},
                       {'implementation': 'new'}):
            with self.subTest(policy=policy):
                self.assertEqual(self.run_case(expected={'kind': 'reject'}, **policy)
                                 ['failed_indices'], [0])

    def test_complete_short_parameter_is_schema_bound_and_policy_explicit(self):
        short = PACKET.replace('parameter name="target"', 'target"')
        for protocol in ('json', 'dsml'):
            for implementation in ('legacy',):
                with self.subTest(protocol=protocol, implementation=implementation):
                    expected = CALLS if protocol == 'dsml' else {'kind': 'reject'}
                    result = self.run_case(reply=short, expected=expected,
                                           protocol=protocol, implementation=implementation)
                    self.assertEqual(result['failed_indices'], [])
                    self.assertEqual(result['matched_outcomes']['calls'], int(protocol == 'dsml'))
                    self.assertEqual(result['matched_outcomes']['reject'], int(protocol == 'json'))

    def test_partial_unknown_and_wrong_type_parameters_remain_refused(self):
        variants = [
            PACKET.replace('parameter name="target"', 'name="target"'),
            PACKET.replace('parameter name="target"', 'undeclared"'),
            PACKET.replace('parameter name="target" string="true">/fixture/value',
                           'target" string="false">42'),
            PACKET.replace('</｜DSML｜ parameter>', ''),
        ]
        for reply in variants:
            for protocol in ('json', 'dsml'):
                for implementation in ('legacy',):
                    with self.subTest(reply=reply, protocol=protocol, implementation=implementation):
                        result = self.run_case(reply=reply, expected={'kind': 'reject'},
                                               protocol=protocol, implementation=implementation)
                        self.assertEqual(result['failed_indices'], [])
                        self.assertEqual(result['matched_outcomes'], {'calls': 0, 'text': 0, 'reject': 1})

    def test_well_formed_envelope_still_checks_required_schema_fields(self):
        damaged = PACKET.replace('name="target"', 'name="undeclared"')
        self.assertEqual(self.run_case(reply=damaged, expected={'kind': 'reject'},
                                      protocol='dsml')['failed_indices'], [])

    def test_invalid_diagnostic_is_not_counted_as_a_successful_refusal(self):
        for data in ({}, [], {'reply': [], 'tools': TOOLS, 'finish_reason': 'stop'},
                     {'reply': 'text', 'tools': ['invalid'], 'finish_reason': 'stop'}):
            with self.subTest(data=data):
                result = self.run_case(data=data, expected={'kind': 'reject'}, protocol='dsml')
                self.assertEqual(result['failed_indices'], [0])
                self.assertEqual(result['matched_outcomes'], {'calls': 0, 'text': 0, 'reject': 0})

    def test_matched_refusal_and_plain_text_are_separate_from_successful_calls(self):
        result = self.run_case(reply='No action remains.',
                               expected={'kind': 'text', 'text': 'No action remains.'})
        self.assertEqual(result['matched_outcomes'], {'calls': 0, 'text': 1, 'reject': 0})


if __name__ == '__main__':
    unittest.main()
