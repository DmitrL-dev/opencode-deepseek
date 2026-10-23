"""Known complete marker spellings are aliases, never missing-tag repair."""
import json
import unittest

from server.openai_format import ToolCallError
from server.tool_protocol import validate_reply
from server.tool_protocol_new import validate_reply as validate_new

TOOLS = [{'type': 'function', 'function': {'name': name, 'parameters': {
    'type': 'object', 'properties': {key: {'type': 'string'} for key in keys},
    'required': keys, 'additionalProperties': False}}}
    for name, keys in [('bash', ['description', 'command']), ('glob', ['pattern'])]]

MIXED = '''<｜DSML｜ calls>
<｜DSML｜ invoke name="bash">
<｜｜DSML｜｜ parameter name="description" string="true">Inspect fixture</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="command" string="true">printf 'quoted "text"\\n' && pwd</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
<｜DSML｜ invoke name="glob">
<｜｜DSML｜｜ parameter name="pattern" string="true">**/*report*</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>'''


class DsmlVocabularyTests(unittest.TestCase):
    def validate(self, text, **kwargs):
        return validate_reply(text, TOOLS, allow_dsml_wrapper_tolerances=True, **kwargs)

    def test_complete_known_spellings_preserve_every_call_and_argument(self):
        for validator in (validate_reply, validate_new):
            _, calls = validator(MIXED, TOOLS, allow_dsml_wrapper_tolerances=True)
            self.assertEqual([c['function']['name'] for c in calls], ['bash', 'glob'])
            self.assertEqual(json.loads(calls[0]['function']['arguments']), {
                'description': 'Inspect fixture', 'command': 'printf \'quoted "text"\\n\' && pwd'})
            self.assertEqual(json.loads(calls[1]['function']['arguments']), {'pattern': '**/*report*'})

    def test_default_remains_strict(self):
        with self.assertRaises(ToolCallError): validate_reply(MIXED, TOOLS)

    def test_schema_and_atomicity_remain_strict(self):
        variants = [MIXED.replace('name="pattern"', 'name="unknown"'),
                    MIXED.replace('name="glob"', 'name="invented"'),
                    MIXED.replace('</｜｜DSML｜｜ parameter>', '', 1),
                    MIXED.replace('</｜｜DSML｜｜ invoke>', '', 1),
                    MIXED.replace('</｜｜DSML｜｜ calls>', ''),
                    MIXED.replace('｜｜DSML｜｜', '｜DSML｜｜'),
                    MIXED.replace('｜｜DSML｜｜', '｜｜｜DSML｜｜｜'),
                    MIXED.replace('｜｜DSML｜｜', '|DSML|'), MIXED+MIXED]
        for text in variants:
            with self.subTest(text=text), self.assertRaises(ToolCallError): self.validate(text)
        for kwargs in ({'provider':'qwen'}, {'finish_reason':'length'}, {'tool_choice':'none'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ToolCallError): self.validate(MIXED, **kwargs)

    def test_quoted_code_and_ambiguous_argument_framing_remain_inert(self):
        for text in ('```xml\n'+MIXED+'\n```','\n'.join('> '+line for line in MIXED.splitlines())):
            self.assertIsNone(self.validate(text)[1])
        with self.assertRaises(ToolCallError):
            self.validate(MIXED.replace('Inspect fixture','Literal <｜DSML｜ invoke name="bad">'))

    def test_retained_incomplete_parameter_header_never_infers_argument_name(self):
        # A later real step omitted `parameter name=` and used a closing tag
        # where a parameter opener was required. Neither can be reconstructed.
        raw = '''<｜DSML｜ calls>
<｜DSML｜ invoke name="glob">
<｜DSML｜ pattern" string="true">**/*report*</｜DSML｜ parameter>
</｜DSML｜｜ parameter name="path" string="true">fixture</｜DSML｜｜ parameter>
</｜DSML｜｜ invoke>
</｜DSML｜ calls>'''
        for validator in (validate_reply, validate_new):
            with self.subTest(validator=validator.__module__), self.assertRaises(ToolCallError):
                validator(raw, TOOLS, allow_dsml_wrapper_tolerances=True,
                          allow_dsml_preamble=True)
