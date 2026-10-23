"""No partial get_goal delivery from a batch with malformed/unknown bash args."""
import unittest
import json
from unittest.mock import patch

from chat_protocol import Reply
from server import api, api_new
from server.dsml import decode
from server.openai_format import ToolCallError
from server.schemas import ChatCompletionRequest
from server.tool_schema import validate_arguments
from server.tool_protocol import validate_reply
from server.tool_protocol_new import validate_reply as validate_new

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
        for validate in (validate_reply,validate_new):
            for native in (False,True):
                with self.subTest(api=validate.__module__,native=native),self.assertRaises(ToolCallError):
                    validate(PACKET,TOOLS,allow_dsml_wrapper_tolerances=native)

    def test_valid_json_still_cannot_invent_an_arguments_wrapper(self):
        raw=PACKET.replace('"}}','"}')
        for validate in (validate_reply,validate_new):
            with self.subTest(api=validate.__module__),self.assertRaises(ToolCallError):
                validate(raw,TOOLS,allow_dsml_wrapper_tolerances=True)

    def test_typed_wrapper_before_valid_read_is_rejected_atomically_without_replay(self):
        # Sanitized from a complete live response: valid JSON inside a DSML
        # parameter does not make that parameter an advertised tool field.
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
        for validate, module in ((api._validate_tool_reply, api),
                                 (api_new.validate_tool_reply, api_new)):
            with self.subTest(contract=module.__name__), \
                    patch('server.config.DEEPSEEK_TOOL_PROTOCOL', 'json'), \
                    patch.object(module, 'save_tool_failure') as saved, \
                    patch.object(api, '_chat_with_retry',
                                 side_effect=AssertionError('Unexpected generation')):
                with self.assertRaises(ToolCallError) as caught:
                    validate(Reply(raw, 'fixture:2', 'stop'), request,
                             allow_continuation=False)
                self.assertEqual(caught.exception.kind, 'schema')
                self.assertEqual(caught.exception.tool, 'bash')
                saved.assert_called_once()
                self.assertEqual(saved.call_args.args[0].text, raw)
