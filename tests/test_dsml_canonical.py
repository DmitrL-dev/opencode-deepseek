"""Literal V4.1 framing reference and malformed-marker rejection.

Vocabulary source: deepseek-ai/DeepSeek-V4.1-Flash, encoding/README.md.
Fixtures deliberately do not import decoder tokens or framing constructors.
"""
import json
import unittest
from unittest.mock import patch

from chat_protocol import Reply
from server import api
from server.openai_format import ToolCallError, _tool_wire_rules, has_tool_call_markup
from server.schemas import ChatCompletionRequest
from server.tool_protocol import validate_reply
from tests.test_tools import FLAT_TOOLS

REFERENCE = '''<｜DSML｜ calls>
<｜DSML｜ invoke name="bash">
<｜DSML｜ parameter name="command" string="true">printf 'owned\\nfixture'</｜DSML｜ parameter>
<｜DSML｜ parameter name="description" string="true">Inspect owned fixture</｜DSML｜ parameter>
</｜DSML｜ invoke>
</｜DSML｜ calls>'''

# Observed shape: asymmetric delimiters, then a generic parameter closure and
# no invocation closure. No repair may infer either missing structural level.
MALFORMED = '''<｜DSML｜｜ calls>
<｜DSML｜｜ invoke name="bash">
<｜DSML｜｜ parameter name="command" string="true">pwd</｜DSML｜｜ parameter>
<｜DSML｜｜ parameter name="description" string="true">Inspect owned fixture</｜DSML｜｜>
<｜DSML｜｜ invoke name="bash">
<｜DSML｜｜ parameter name="command" string="true">ls -la</｜DSML｜｜ parameter>
<｜DSML｜｜ parameter name="description" string="true">Inspect owned directory</｜DSML｜｜>
</｜DSML｜｜>
</｜DSML｜｜ calls>'''


class CanonicalDsmlTests(unittest.TestCase):
    def test_prompt_uses_literal_v41_reference_delimiters(self):
        rules = _tool_wire_rules('dsml')
        self.assertIn('<｜DSML｜ calls>', rules)
        self.assertIn('</｜DSML｜ parameter>', rules)
        self.assertNotIn('｜｜DSML｜｜', rules)

    def test_reference_and_retained_legacy_vocabulary_preserve_exact_inputs(self):
        for raw in (REFERENCE, REFERENCE.replace('｜DSML｜', '｜｜DSML｜｜')):
            for validator in (validate_reply,):
                with self.subTest(raw=raw, validator=validator.__module__):
                    _, calls = validator(raw, FLAT_TOOLS)
                    self.assertEqual(json.loads(calls[0]['function']['arguments']), {
                        'command': "printf 'owned\\nfixture'", 'description': 'Inspect owned fixture'})

    def test_damaged_marker_family_is_an_error_in_both_contracts(self):
        variants = [MALFORMED, REFERENCE.replace('｜DSML｜', '|DSML|'),
                    REFERENCE.replace('｜DSML｜', '｜｜｜DSML｜｜｜'),
                    REFERENCE.replace('｜DSML｜', '｜｜DSML｜'),
                    REFERENCE.replace('｜DSML｜', '｜DSML｜｜'),
                    REFERENCE.replace('</｜DSML｜ invoke>', '</｜DSML｜>'),
                    REFERENCE.replace('<｜DSML｜ invoke', '<｜｜DSML｜｜ invoke', 1)]
        for raw in variants:
            self.assertTrue(has_tool_call_markup(raw))
            for validator in (validate_reply,):
                with self.subTest(raw=raw, validator=validator.__module__), self.assertRaises(ToolCallError):
                    validator(raw, FLAT_TOOLS)

    def test_ambiguous_closers_still_fail_with_every_wrapper_tolerance_enabled(self):
        for raw in (MALFORMED, MALFORMED.replace('｜DSML｜｜', '｜DSML｜')):
            for validator in (validate_reply,):
                with self.subTest(validator=validator.__module__, raw=raw), self.assertRaises(ToolCallError):
                    validator(raw, FLAT_TOOLS, allow_dsml_preamble=True,
                              allow_dsml_wrapper_tolerances=True)

    def test_canonical_packet_still_requires_all_schemas_choice_and_normal_finish(self):
        for validator in (validate_reply,):
            for kwargs in ({'provider':'qwen'}, {'finish_reason':'length'}, {'tool_choice':'none'}):
                with self.subTest(validator=validator.__module__, kwargs=kwargs), self.assertRaises(ToolCallError):
                    validator(REFERENCE, FLAT_TOOLS, **kwargs)
            for raw in (REFERENCE.replace('name="command"','name="unknown"'),
                        REFERENCE.replace('name="bash"','name="invented"'),
                        REFERENCE+'\n'+REFERENCE):
                with self.subTest(validator=validator.__module__, raw=raw), self.assertRaises(ToolCallError):
                    validator(raw, FLAT_TOOLS)

    def test_quoted_and_fenced_marker_families_remain_data(self):
        for value in (REFERENCE, MALFORMED):
            variants = ['The literal marker is '+value.splitlines()[0]+'.',
                        '```xml\n'+value+'\n```', '````\n'+value+'\n````',
                        '~~~text\n'+value+'\n~~~',
                        '\n'.join('> '+s for s in value.splitlines()),
                        '\n'.join('    '+s for s in value.splitlines())]
            for raw in variants:
                self.assertFalse(has_tool_call_markup(raw))
                for validator in (validate_reply,):
                    with self.subTest(validator=validator.__module__, raw=raw):
                        _, calls = validator(raw, FLAT_TOOLS)
                        self.assertIsNone(calls)

    def test_broken_json_containing_damaged_native_markers_cannot_be_demoted(self):
        for body in (MALFORMED, '\n'.join('    '+line for line in MALFORMED.splitlines())):
            raw='```tool_calls\n[{\n'+body+'\n```'
            for validator in (validate_reply,):
                with self.subTest(validator=validator.__module__, body=body),self.assertRaises(ToolCallError):
                    validator(raw,FLAT_TOOLS)

    def test_both_apis_save_private_failure_and_never_generate_a_repair(self):
        req=ChatCompletionRequest(model='deepseek-expert',messages=[],tools=FLAT_TOOLS)
        for validator, module in ((api._validate_tool_reply, api),):
            with self.subTest(validator=validator.__module__), \
                    patch('server.config.DEEPSEEK_TOOL_PROTOCOL','dsml'), \
                    patch.object(module,'save_tool_failure') as saved, \
                    patch.object(api,'_chat_with_retry',side_effect=AssertionError('No generation')):
                with self.assertRaises(ToolCallError):
                    validator(Reply(MALFORMED,'fixture:6','stop'),req,allow_continuation=False)
                saved.assert_called_once()
