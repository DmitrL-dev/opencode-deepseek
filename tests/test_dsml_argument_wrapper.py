"""No partial get_goal delivery from a batch with malformed/unknown bash args."""
import unittest
import json
from unittest.mock import patch

from chat_protocol import Reply
from server import api
from server.dsml import decode
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from server.tool_schema import validate_arguments
from server.tool_protocol import validate_reply

TOOLS = [{'type':'function','function':{'name':name,'parameters':{
    'type':'object','properties':{key:{'type':'string'} for key in keys},
    'required':keys,'additionalProperties':False}}}
    for name,keys in [('get_goal',[]),('bash',['command','description'])]]

PACKET = '''<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="get_goal">
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="bash">
<｜｜DSML｜｜ parameter name="arguments" string="false">{"description":"Run focused tests","command":"python -m pytest -q tests/test_fixture.py"}}</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>'''


class DsmlArgumentWrapperTests(unittest.TestCase):
    def test_extra_json_brace_rejects_entire_batch_including_valid_first_call(self):
        for validate in (validate_reply,):
            for native in (False,True):
                with self.subTest(api=validate.__module__,native=native),self.assertRaises(ToolCallError):
                    validate(PACKET,TOOLS,allow_dsml_wrapper_tolerances=native)

    def test_complete_typed_input_object_uses_the_same_advertised_schema(self):
        raw=PACKET.replace('"}}','"}')
        for validate in (validate_reply,):
            with self.subTest(api=validate.__module__):
                _, calls = validate(raw,TOOLS,allow_dsml_wrapper_tolerances=True)
                self.assertEqual([c['function']['name'] for c in calls], ['get_goal', 'bash'])
                self.assertEqual(json.loads(calls[1]['function']['arguments']), {
                    'description': 'Run focused tests',
                    'command': 'python -m pytest -q tests/test_fixture.py'})

    def test_complete_typed_wrapper_and_read_validate_without_generation(self):
        # Sanitized from a complete live response. The inert decoder preserves
        # the wire object; the schema-aware envelope layer resolves its meaning.
        arguments = {'description': 'Find report documentation', 'command': 'ls docs'}
        tools = [*TOOLS, {'type': 'function', 'function': {'name': 'read',
            'parameters': {'type': 'object', 'properties': {
                'file_path': {'type': 'string'}, 'offset': {'type': 'number'},
                'limit': {'type': 'number'}}, 'required': ['file_path']}}}]
        raw = '''<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="bash">
<｜｜DSML｜｜ parameter name="arguments" string="false">%s</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="read">
<｜｜DSML｜｜ parameter name="file_path" string="true">/workspace/cli.py</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="offset" string="false">955</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="limit" string="false">140</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>''' % json.dumps(arguments)
        decoded = decode(raw, json.loads, allow_wrapper_tolerances=True)
        self.assertEqual(decoded[0]['arguments'], {'arguments': arguments})
        validate_arguments('bash', arguments, tools)
        validate_arguments('read', decoded[1]['arguments'], tools)
        request = ChatCompletionRequest(model='deepseek-expert', messages=[], tools=tools)
        for validate, module in ((api._validate_tool_reply, api),):
            with self.subTest(contract=module.__name__), \
                    patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'json'), \
                    patch.object(module, 'save_tool_failure') as saved, \
                    patch.object(api, '_chat_with_retry',
                                 side_effect=AssertionError('Unexpected generation')):
                reply, _, calls = validate(Reply(raw, 'fixture:2', 'stop'), request,
                                          allow_continuation=False)
                self.assertEqual(reply.conversation_id, 'fixture:2')
                self.assertEqual([c['function']['name'] for c in calls], ['bash', 'read'])
                self.assertEqual(json.loads(calls[0]['function']['arguments']), arguments)
                self.assertEqual(json.loads(calls[1]['function']['arguments']),
                                 decoded[1]['arguments'])
                saved.assert_not_called()
