import json
import re
import unittest

from server.openai_format import (
    ToolCallError, enforce_tool_choice, looks_truncated, messages_to_prompt,
    parse_tool_calls, validate_tool_choice,
)
from server.schemas import ChatMessage


def block(calls):
    return "```tool_calls\n" + json.dumps(calls) + "\n```"


FLAT_TOOLS = [{"type": "function", "function": {"name": name, "parameters": {
    "type": "object", "properties": properties, "required": required}}}
    for name, properties, required in (
        ("read", {"file_path": {"type": "string"}, "offset": {"type": "number"},
                  "limit": {"type": "number"}}, ["file_path"]),
        ("bash", {"command": {"type": "string"}, "description": {"type": "string"}},
         ["command", "description"]))]


class ToolProtocolTests(unittest.TestCase):
    def test_missing_outer_array_closer_preserves_complete_objects_only(self):
        items = [{"name": "todo_write", "arguments": {"todos": [
            {"content": 'quoted "todo" and \\slash }]}', "status": "pending"}]}}]
        for batch in (items, items * 2):
            text = '```tool_calls\n' + json.dumps(batch)[:-1] + '\n```'
            with self.assertRaises(ToolCallError):
                parse_tool_calls(text, {'todo_write'})
            _, calls = parse_tool_calls(text, {'todo_write'}, allow_missing_array_closer=True)
            self.assertEqual([json.loads(c['function']['arguments']) for c in calls],
                             [item['arguments'] for item in batch])
        for payload in ('[{"name":"todo_write","arguments":{}},',
                        '[{"name":"todo_write","arguments":{}},{',
                        '[{"name":"todo_write","arguments":{"todos":[{}}',
                        '[{"name":"todo_write","arguments":{"content":"unfinished}',
                        '[{"name":"todo_write","arguments":{}} trailing',
                        '[{"name":"unknown","arguments":{}}'):
            with self.subTest(payload=payload), self.assertRaises(ToolCallError):
                parse_tool_calls('```tool_calls\n' + payload + '\n```', {'todo_write'},
                                 allow_missing_array_closer=True)

    def test_observed_dsml_closers_after_json_never_authorize_other_markup(self):
        arguments = {"command": 'printf "%s\\n" "fixture"', "description": "Print fixture"}
        payload = json.dumps([{"name": "bash", **arguments}])
        suffix = '</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
        for closer in ('', '}'):
            for fence in ('', '\n```'):
                with self.subTest(closer=closer, fence=fence):
                    text = '```tool_calls\n' + payload + closer + suffix + fence
                    with self.assertRaises(ToolCallError):
                        parse_tool_calls(text, {'bash'}, allow_unclosed_fence=True, flat_argument_tools=FLAT_TOOLS)
                    _, calls = parse_tool_calls(text, {'bash'}, allow_unclosed_fence=True,
                                                allow_closing_artifacts=True, flat_argument_tools=FLAT_TOOLS)
                    self.assertEqual(json.loads(calls[0]['function']['arguments']), arguments)
        for bad in (suffix + ' text', suffix + payload, '<invoke>' + suffix,
                    suffix.replace('parameter>', 'parameter>private'), suffix.splitlines()[0]):
            with self.subTest(bad=bad), self.assertRaises(ToolCallError):
                parse_tool_calls('```tool_calls\n' + payload + bad, {'bash'}, allow_unclosed_fence=True,
                                 allow_closing_artifacts=True, flat_argument_tools=FLAT_TOOLS)

    def test_dsml_tool_calls_closer_preserves_complete_batch_only(self):
        items = [{"name": "bash", "command": "pwd && ls -la", "description": "List files"},
                 {"name": "bash", "command": "git status --short", "description": "Status"}]
        payload = json.dumps(items)
        suffix = "</｜｜DSML｜｜tool_calls>"
        for fence in ("", "\n```"):
            _, calls = parse_tool_calls("```tool_calls\n" + payload + suffix + fence,
                                       {"bash"}, allow_unclosed_fence=True,
                                       allow_closing_artifacts=True, flat_argument_tools=FLAT_TOOLS)
            self.assertEqual([json.loads(c["function"]["arguments"]) for c in calls],
                             [{k: v for k, v in item.items() if k != "name"} for item in items])
        for body in (payload[:-1] + suffix, payload + suffix + " prose",
                     payload + suffix + payload, payload + suffix * 2,
                     payload + "<｜｜DSML｜｜tool_calls>" + suffix):
            with self.subTest(body=body), self.assertRaises(ToolCallError):
                parse_tool_calls("```tool_calls\n" + body, {"bash"},
                                 allow_unclosed_fence=True, allow_closing_artifacts=True,
                                 flat_argument_tools=FLAT_TOOLS)

    def test_flat_inputs_preserve_values_and_batch_order_with_advertised_schemas(self):
        original = [{"name": "read", "file_path": 'a/"quoted"\\path', "offset": 1, "limit": 60},
                    {"name": "bash", "command": 'printf "%s\\n" "quoted"\n# literal }]}',
                     "description": "Read the owned fixture"}]
        with self.assertRaises(ToolCallError):
            parse_tool_calls(block(original), {"read", "bash"})
        _, calls = parse_tool_calls(block(original), {"read", "bash"}, flat_argument_tools=FLAT_TOOLS)
        self.assertEqual([c['function']['name'] for c in calls], ['read', 'bash'])
        self.assertEqual([json.loads(c['function']['arguments']) for c in calls],
                         [{k: v for k, v in item.items() if k != 'name'} for item in original])

    def test_flat_inputs_never_infer_missing_fields_or_drop_ambiguous_fields(self):
        good = {"name": "read", "file_path": "fixture"}
        bad = [{"name": "read"}, {"name": "read", "offset": 1},
               {**good, "unexpected": "private"}, {**good, "arguments": None},
               {**good, "arguments": {}}, {**good, "function": {}},
               {**good, "id": "private"}, {"name": "unknown", "file_path": "fixture"},
               {"name": "read", "arguments": [], "file_path": "fixture"},
               {"function": {"name": "read", "file_path": "fixture"}},
               {"name": "read", "function": {"name": "read", "arguments": {}}}]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ToolCallError):
                parse_tool_calls(block([good, item]), {"read", "bash"}, flat_argument_tools=FLAT_TOOLS)
        for schema in ({}, {"type": "object"}, {"type": "array"},
                       {"type": "object", "properties": {"name": {}, "file_path": {}}},
                       {"type": "object", "properties": {"file_path": {}}, "required": "file_path"}):
            with self.subTest(schema=schema), self.assertRaises(ToolCallError):
                parse_tool_calls(block([good]), {"read"},
                                 flat_argument_tools=[{"name": "read", "parameters": schema}])
        with self.assertRaises(ToolCallError):
            parse_tool_calls(block([good]), {"read"}, flat_argument_tools=FLAT_TOOLS * 2)

    def test_normal_stop_envelope_tolerances_compose_without_changing_arguments(self):
        arguments = {"command": 'printf "%s\\n" "quoted"\n# literal }]}',
                     "description": "Print fixture"}
        for flat in (False, True):
            item = {"name": "bash", **arguments} if flat else {"name": "bash", "arguments": arguments}
            for fence in (False, True):
                for closer in (False, True):
                    text = '```tool_calls\n' + json.dumps([item]) + ('}' if closer else '') + ('\n```' if fence else '')
                    with self.subTest(flat=flat, fence=fence, closer=closer):
                        _, calls = parse_tool_calls(text, {'bash'}, allow_unclosed_fence=True,
                                                    allow_closing_artifacts=True, flat_argument_tools=FLAT_TOOLS)
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(json.loads(calls[0]['function']['arguments']), arguments)
        for tail in ('}}', '} trailing', ']'):
            with self.subTest(tail=tail), self.assertRaises(ToolCallError):
                parse_tool_calls('```tool_calls\n' + json.dumps([{"name": "bash", **arguments}]) + tail,
                                 {'bash'}, allow_unclosed_fence=True, allow_closing_artifacts=True,
                                 flat_argument_tools=FLAT_TOOLS)

    def test_observed_redundant_closer_preserves_every_call_and_argument(self):
        commands = [
            'printf "%s\\n" "quoted"',
            'echo "literal }]}"\nprintf "second line"',
            'python -m unittest tests.test_tools',
        ]
        original = [{"name": "bash", "arguments": {"command": command, "description": "fixture"}}
                    for command in commands]
        text = '```tool_calls\n' + json.dumps(original) + '}\n```'
        with self.assertRaises(ToolCallError):
            parse_tool_calls(text, {"bash"})
        _, calls = parse_tool_calls(text, {"bash"}, allow_closing_artifacts=True)
        self.assertEqual([json.loads(c['function']['arguments']) for c in calls],
                         [item['arguments'] for item in original])

    def test_redundant_closer_does_not_enable_general_json_repair(self):
        good = '[{"name":"bash","arguments":{"command":"echo fixture"}}]'
        payloads = [good + tail for tail in (']', '}}', '} prose', '}{}', ',{}', '{}', '\n' + good)]
        payloads += [good[:-1] + ',{"name":"bash","arguments":{}}',
                     '[{"name":"unknown","arguments":{}}]}',
                     '[{"name":"bash","arguments":{"command":"raw\nnewline"}}]}',
                     '[{"name":"bash","arguments":{}},{"name":"bash","arguments":[]}]}']
        for body in payloads:
            with self.subTest(body=body), self.assertRaises(ToolCallError):
                parse_tool_calls('```tool_calls\n' + body + '\n```', {'bash'}, allow_closing_artifacts=True)

    def test_confirmed_stop_can_omit_only_the_markdown_closing_fence(self):
        arguments = {"command": 'printf "%s\\n" "quoted"\n# ``` stays data'}
        text = block([{"name": "bash", "arguments": arguments}])[:-4]
        with self.assertRaises(ToolCallError):
            parse_tool_calls(text, {"bash"})
        content, calls = parse_tool_calls(text, {"bash"}, allow_unclosed_fence=True)
        self.assertEqual(content, "")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), arguments)

    def test_missing_wrapper_never_repairs_json_or_accepts_a_partial_batch(self):
        good = '[{"name":"bash","arguments":{}}]'
        payloads = [good[:-1] + ',{"name":"bash","arguments":{',
                    good + '\nextra', good + '\n' + good, good + '\n```json\n{}\n```',
                    '[{"name":"unknown","arguments":{}}]',
                    '[{"name":"bash","arguments":{"command":"raw\nnewline"}}]']
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ToolCallError):
                parse_tool_calls('```tool_calls\n' + payload, {"bash"}, allow_unclosed_fence=True)

    def test_prompt_examples_are_valid_json_and_require_real_schema_arguments(self):
        prompt = messages_to_prompt([ChatMessage(role="user", content="edit a file")], tools=[{
            "type": "function", "function": {"name": "write", "parameters": {
                "type": "object", "required": ["path", "content"], "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}}}}}])
        examples = re.findall(r"```tool_calls\n(.*?)\n```", prompt, re.DOTALL)
        self.assertEqual(len(examples), 1)
        self.assertIsInstance(json.loads(examples[0])[0]["arguments"], dict)
        self.assertIn("including all required fields", prompt)
        literal = prompt.split("A valid JSON string example is: ", 1)[1].splitlines()[0]
        self.assertEqual(json.loads(literal), 'first line\n"quoted text" and a \\backslash')
        for choice in ('required', {"type": "function", "function": {"name": "write"}}):
            forced = messages_to_prompt([ChatMessage(role="user", content="write")],
                                        tools=[{"name": "write", "parameters": {}}], tool_choice=choice)
            self.assertTrue(forced.rsplit('Assistant:', 1)[0].rstrip().endswith(
                'Reply with ONLY the fenced tool_calls block.'))

    def test_json_diagnostics_report_only_category_and_position(self):
        bodies = [
            ('[{"name":"write","arguments":{"content":"private\nvalue"}}]',
             "unescaped_control_character"),
            ('[{"name":"write","arguments":{"content":"private\\q"}}]', "invalid_escape"),
            ('[{"name":"write","arguments":{"content":"private', "unterminated_string"),
            ('[{"name":"write","arguments":{"content":"private",}}]', "invalid_syntax"),
        ]
        for body, category in bodies:
            with self.subTest(category=category), self.assertRaises(ToolCallError) as caught:
                parse_tool_calls("```tool_calls\n" + body + "\n```", {"write"})
            message = str(caught.exception)
            self.assertIn(category, message)
            self.assertRegex(message, r"line \d+, column \d+")
            self.assertNotIn("private", message)
            self.assertIn("automatic replay is disabled", message)

    def test_commands_preserve_escaped_payload_byte_for_byte(self):
        arguments = {"command": 'printf "%s\\n" "hello"\ncat <<\'EOF\'\n```\nEOF',
                     "content": 'line 1\r\n\t"quoted" \\path русский'}
        _, calls = parse_tool_calls(block([{"name": "bash", "arguments": arguments}]), {"bash"})
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), arguments)

    def test_examples_and_native_markup_remain_text(self):
        samples = [
            'Do not execute `bash(command="echo audit")`.',
            '```json\n{"name":"bash","arguments":{"command":"echo audit"}}\n```',
            '{"name":"bash","arguments":{"command":"echo audit"}}',
            '<tool_call><function name="bash"><parameter name="command">echo audit</parameter></function></tool_call>',
            'function<|tool_sep|>bash {"command":"echo audit"}',
            'An example follows:\n' + block([{"name":"bash","arguments":{}}]),
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(parse_tool_calls(text, {"bash"}), (text, None))

    def test_complete_batch_preserves_order_and_validates_string_arguments(self):
        text = block([{"name":"write","arguments":{"filePath":"a","content":"hello"}},
                      {"name":"bash","arguments":json.dumps({"command":"echo audit"})}])
        content, calls = parse_tool_calls(text, {"write","bash"})
        self.assertEqual(content, "")
        self.assertEqual([c["function"]["name"] for c in calls], ["write","bash"])
        self.assertEqual(json.loads(calls[1]["function"]["arguments"]), {"command":"echo audit"})
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])

    def test_partial_batch_never_returns_first_call(self):
        text = '```tool_calls\n[{"name":"write","arguments":{}},{"name":"bash","arguments":{"command":"echo'
        self.assertTrue(looks_truncated(text, {"write","bash"}))
        with self.assertRaises(ToolCallError):
            parse_tool_calls(text, {"write","bash"})

    def test_bad_member_rejects_entire_batch(self):
        bad_members = [
            {"name":"unknown","arguments":{}},
            {"name":"write","arguments":'{"filePath":"cut'},
            {"name":"write","arguments":[]},
            {"name":"write"},
            {"name":"write","arguments":{"value":float("nan")}},
            "not a call",
        ]
        for member in bad_members:
            with self.subTest(member=member), self.assertRaises(ToolCallError):
                parse_tool_calls(block([{"name":"write","arguments":{}}, member]), {"write"})

    def test_empty_allowlist_allows_no_actions(self):
        with self.assertRaises(ToolCallError):
            parse_tool_calls(block([{"name":"write","arguments":{}}]), set())

    def test_closed_invalid_protocol_does_not_trigger_continuation(self):
        for text in ['```tool_calls\n[broken]\n```', '```python\nprint("unfinished")',
                     'Ordinary prose with an unmatched [', '```tool_calls\n[]\n```\nextra']:
            self.assertFalse(looks_truncated(text))
        with self.assertRaises(ToolCallError):
            parse_tool_calls('```tool_calls\n[broken]\n```', {"write"})

    def test_markdown_inside_cut_arguments_does_not_hide_truncation(self):
        self.assertTrue(looks_truncated('```tool_calls\n[{"name":"write","arguments":{"content":"```code'))

    def test_tool_choice_is_enforced(self):
        _, bash = parse_tool_calls(block([{"name":"bash","arguments":{}}]), {"bash","write"})
        for choice, calls in [("none",bash),("required",None),
                              ({"type":"function","function":{"name":"write"}},bash)]:
            with self.subTest(choice=choice), self.assertRaises(ToolCallError):
                enforce_tool_choice(choice, calls)
        enforce_tool_choice("auto", bash)
        enforce_tool_choice("none", None)
        enforce_tool_choice({"type":"function","function":{"name":"bash"}}, bash)

    def test_request_policy_requires_advertised_tools(self):
        for choice, names in [("required",set()), ("unsupported",{"bash"}),
                              ({"type":"function","function":{"name":"write"}},{"bash"})]:
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                validate_tool_choice(choice, names)

    def test_out_of_order_tool_results_keep_call_identity(self):
        prompt = messages_to_prompt([
            ChatMessage(role="assistant", tool_calls=[
                {"id":"call_a","function":{"name":"read","arguments":'{"filePath":"a"}'}},
                {"id":"call_b","function":{"name":"read","arguments":'{"filePath":"b"}'}},
            ]),
            ChatMessage(role="tool",tool_call_id="call_b",content="second file"),
            ChatMessage(role="tool",tool_call_id="call_a",content="first file"),
        ])
        history = re.findall(r"```tool_calls\n(.*?)\n```", prompt, re.DOTALL)
        calls = json.loads(history[0])
        self.assertEqual(calls[0], {"id": "call_a", "name": "read", "arguments": {"filePath": "a"}})
        self.assertEqual(calls[1]["id"], "call_b")
        self.assertIn('Tool (read, call_id=call_b): second file', prompt)
        self.assertIn('Tool (read, call_id=call_a): first file', prompt)

    def test_historical_write_arguments_survive_prompt_rendering_byte_for_byte(self):
        content = (
            'line one\n'
            '  indented "quoted" text and \\backslash\\ path\n'
            'Unicode: русский 中文 😀\n'
            '```python\n'
            'print("nested fence")\n'
            '```\n'
            'trailing\\'
        )
        arguments = {"filePath": "notes/пример.md", "content": content}
        prompt = messages_to_prompt([
            ChatMessage(role="assistant", tool_calls=[
                {"id": "call_write_1", "type": "function",
                 "function": {"name": "write", "arguments": json.dumps(arguments, ensure_ascii=False)}}]),
            ChatMessage(role="tool", tool_call_id="call_write_1", name="write", content="ok"),
        ])
        history = re.findall(r"```tool_calls\n(.*?)\n```", prompt, re.DOTALL)
        self.assertEqual(len(history), 1)
        calls = json.loads(history[0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "call_write_1")
        self.assertEqual(calls[0]["name"], "write")
        self.assertEqual(calls[0]["arguments"], arguments)
        self.assertEqual(calls[0]["arguments"]["content"], content)
        self.assertIn(json.dumps(arguments, ensure_ascii=False), history[0])
        self.assertEqual(prompt.count("```python"), 1)
        self.assertIn("Tool (write, call_id=call_write_1): ok", prompt)
