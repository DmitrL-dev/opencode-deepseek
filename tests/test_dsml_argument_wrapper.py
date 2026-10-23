"""No partial get_goal delivery from a batch with malformed/unknown bash args."""
import unittest

from server.openai_format import ToolCallError
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
