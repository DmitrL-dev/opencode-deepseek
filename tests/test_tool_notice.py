"""Browser providers: an attempt at a tool call is validated like in any agent.

Valid calls run and the text around them is kept. An unknown tool or arguments
that do not fit the schema stop the message: one pointed correction (repair on)
and then the model's raw reply plus a one-line notice reaches the agent as text.
"""
import json
import unittest
from unittest.mock import Mock, patch

from chat_protocol import Reply
from server import api
from server.conversation_cache import ConversationStateError
from server.schemas import ChatCompletionRequest, ChatMessage
from server.tool_notice import NOTICE_PREFIX
from tests.support import isolated_access
from tests.test_tools import block

QUESTION_TOOL = {"type": "function", "function": {
    "name": "ask_user_question", "description": "PRIVATE-DOC",
    "parameters": {"type": "object", "required": ["questions"], "properties": {
        "questions": {"type": "array", "items": {
            "type": "object", "required": ["question", "options"], "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {
                    "type": "object", "required": ["label"],
                    "properties": {"label": {"type": "string"}}}}}}}}}}}
OTHER_TOOL = {"type": "function", "function": {
    "name": "read_file", "description": "OTHER-PRIVATE-DOC",
    "parameters": {"type": "object", "required": ["path"], "properties": {
        "path": {"type": "string"}, "limit": {"type": "integer"}}}}}
SHAPE = '{questions: [{question: string, options: [{label: string}]}]}'
SECRET = "SECRET-ARGUMENT-VALUE"
BAD = block([{"name": "ask_user_question", "arguments": {"question": SECRET, "options": ["a"]}}])
GOOD = block([{"name": "ask_user_question", "arguments": {"questions": [
    {"question": "Drink?", "options": [{"label": "Tea"}]}]}}])
FAKE = block([{"name": "made_up", "arguments": {"x": SECRET}}])


class ToolNoticeTests(unittest.TestCase):
    def setUp(self):
        isolated_access(self)
        patcher = patch.object(api, '_FORMAT_REPAIR', False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.req = ChatCompletionRequest(
            model='claude-web', tools=[QUESTION_TOOL, OTHER_TOOL],
            messages=[ChatMessage(role='user', content='ask me something')])

    def chat(self, replies, req, prompt='p'):
        with patch.object(api, '_chat_with_retry', side_effect=replies) as chat:
            return api._conversation_tool_chat(prompt, req, None), chat

    def follow(self, content, user='retry please'):
        history = [*self.req.messages, ChatMessage(role='assistant', content=content),
                   ChatMessage(role='user', content=user)]
        return self.req.model_copy(update={'messages': history})

    def test_schema_failure_is_raw_reply_plus_a_short_notice_and_the_chat_continues(self):
        (reply, content, calls), chat = self.chat([Reply(BAD, 'claude:1')], self.req)
        self.assertEqual(chat.call_count, 1)
        self.assertIsNone(calls)
        # The model's own text first, then one notice line: no shapes, no tool list.
        self.assertTrue(content.startswith(BAD))
        notice = content[len(BAD):].strip()
        self.assertTrue(notice.startswith(NOTICE_PREFIX))
        self.assertIn('`ask_user_question`', notice)
        self.assertIn('NOT executed', notice)
        for leak in (SHAPE, 'read_file', 'PRIVATE-DOC', 'Expected arguments'):
            self.assertNotIn(leak, notice)

        follow = self.follow(content)
        (second, _, second_calls), chat = self.chat([Reply(GOOD, 'claude:3')], follow, 'unused')
        self.assertEqual(second_calls[0]['function']['name'], 'ask_user_question')
        sent = chat.call_args.args[0]
        self.assertEqual(chat.call_args.args[1].conversation_id, 'claude:1')
        # The model receives the notice (not its own raw reply again) with the follow-up.
        self.assertIn('NOT executed', sent)
        self.assertIn('retry please', sent)
        self.assertNotIn(SECRET, sent)
        self.assertNotIn('ask me something', sent)
        # The link was consumed by that continuation: no replay.
        with patch.object(api, '_chat_with_retry') as again, self.assertRaises(api.ToolCallError):
            api._conversation_tool_chat('dup', follow, None)
        again.assert_not_called()

    def test_repair_on_wrong_arguments_get_one_pointed_correction(self):
        with patch.object(api, '_FORMAT_REPAIR', True):
            (_, content, calls), chat = self.chat(
                [Reply(BAD, 'claude:1'), Reply(GOOD, 'claude:1')], self.req)
        self.assertEqual(calls[0]['function']['name'], 'ask_user_question')
        self.assertEqual(content, '')                  # the agent sees only the call
        self.assertEqual(chat.call_count, 2)
        prompt, correction_req = chat.call_args_list[1].args[:2]
        self.assertEqual(correction_req.conversation_id, 'claude:1')
        # This tool's shape only: no values, no other tool, no schema dump.
        self.assertIn('Expected arguments for `ask_user_question`', prompt)
        self.assertIn(SHAPE, prompt)
        for leak in (SECRET, 'PRIVATE-DOC', 'read_file'):
            self.assertNotIn(leak, prompt)

    def test_repair_on_unknown_tool_gets_the_full_tool_list(self):
        with patch.object(api, '_FORMAT_REPAIR', True):
            (_, _, calls), chat = self.chat([Reply(FAKE, 'claude:1'), Reply(GOOD, 'claude:1')], self.req)
        self.assertEqual(calls[0]['function']['name'], 'ask_user_question')
        prompt = chat.call_args_list[1].args[0]
        self.assertIn('`made_up` is not an available tool', prompt)
        self.assertIn('- ask_user_question: ' + SHAPE, prompt)
        self.assertIn('- read_file: {path: string, limit?: integer}', prompt)
        for leak in (SECRET, 'PRIVATE-DOC'):
            self.assertNotIn(leak, prompt)

    def test_repair_on_second_failure_forwards_the_raw_reply_with_a_notice(self):
        for first, second in ((BAD, BAD), (FAKE, FAKE), (FAKE, BAD)):
            with self.subTest(first=first[:30], second=second[:30]), patch.object(api, '_FORMAT_REPAIR', True):
                (_, content, calls), chat = self.chat(
                    [Reply(first, 'claude:1'), Reply(second, 'claude:1')], self.req)
            self.assertEqual(chat.call_count, 2)       # exactly one correction, no loop
            self.assertIsNone(calls)
            self.assertTrue(content.startswith(second))
            self.assertIn(NOTICE_PREFIX, content)
            self.assertNotIn(SHAPE, content)
            self.assertNotIn('read_file', content)

    def test_repair_off_forwards_at_once_without_a_second_request(self):
        for text, mention in ((BAD, 'the arguments of `ask_user_question`'),
                              (FAKE, '`made_up` is not an available tool')):
            with self.subTest(text=text[:30]):
                (_, content, calls), chat = self.chat([Reply(text, 'claude:1')], self.req)
                self.assertEqual(chat.call_count, 1)
                self.assertIsNone(calls)
                self.assertTrue(content.startswith(text))
                self.assertIn(mention, content)

    def test_abstract_blocks_are_text_with_no_correction(self):
        text = ('Format:\n```tool_calls\n[ { ... }, { ... } ]\n```\n'
                'Placeholder:\n```tool_calls\n[{"name": "tool_name", "arguments": {...}}]\n```')
        for repair in (False, True):
            with self.subTest(repair=repair), patch.object(api, '_FORMAT_REPAIR', repair):
                (_, content, calls), chat = self.chat([Reply(text, 'claude:1')], self.req)
                self.assertEqual(chat.call_count, 1)
                self.assertIsNone(calls)
                self.assertEqual(content, text)

    def test_valid_call_keeps_the_surrounding_text_and_the_history_hash(self):
        text = 'Let me ask.\n\n' + GOOD + '\n\nThat is the question.'
        (_, content, calls), _ = self.chat([Reply(text, 'claude:1')], self.req)
        self.assertEqual(content, 'Let me ask.\n\nThat is the question.')
        answer = json.dumps({"answers": [{"id": "q1", "selected": ["Tea"]}]})
        history = [*self.req.messages,
                   ChatMessage(role='assistant', content=content, tool_calls=calls),
                   ChatMessage(role='tool', tool_call_id=calls[0]['id'], content=answer)]
        follow = self.req.model_copy(update={'messages': history})
        (_, again, again_calls), chat = self.chat([Reply('You chose Tea.', 'claude:2')], follow, 'unused')
        self.assertIsNone(again_calls)
        self.assertEqual(again, 'You chose Tea.')
        # Only the answer goes to the provider: no reminder, no echoed call, no ids.
        self.assertEqual(chat.call_args.args[0], f'Tool result (ask_user_question): {answer}')
        self.assertEqual(chat.call_args.args[1].conversation_id, 'claude:1')

    def test_results_of_one_tool_keep_the_identifier_mapping(self):
        two = block([{"name": "read_file", "arguments": {"path": "a"}},
                     {"name": "read_file", "arguments": {"path": "b"}}])
        (_, content, calls), _ = self.chat([Reply(two, 'claude:1')], self.req)
        history = [*self.req.messages,
                   ChatMessage(role='assistant', content=content, tool_calls=calls),
                   *[ChatMessage(role='tool', tool_call_id=c['id'], content=f'r{i}')
                     for i, c in enumerate(calls)]]
        follow = self.req.model_copy(update={'messages': history})
        _, chat = self.chat([Reply('done', 'claude:2')], follow, 'unused')
        sent = chat.call_args.args[0]
        self.assertIn('Previously requested calls', sent)
        self.assertIn(f"call_id={calls[0]['id']}", sent)

    def test_repeating_the_executed_call_is_delivered_as_a_message_not_run(self):
        (_, content, calls), _ = self.chat([Reply(GOOD, 'claude:1')], self.req)
        history = [*self.req.messages,
                   ChatMessage(role='assistant', content=content, tool_calls=calls),
                   ChatMessage(role='tool', tool_call_id=calls[0]['id'], content='{"answers": []}')]
        follow = self.req.model_copy(update={'messages': history})
        (reply, text, again), _ = self.chat([Reply(GOOD, 'claude:2')], follow, 'unused')
        self.assertIsNone(again)
        self.assertTrue(text.startswith(GOOD))
        self.assertIn('repeats the call that was just executed', text)
        # The chat stays usable: the link of this message exists.
        follow2 = self.req.model_copy(update={'messages': [
            *history, ChatMessage(role='assistant', content=text),
            ChatMessage(role='user', content='go on')]})
        (_, ok, ok_calls), chat = self.chat([Reply('Fine.', 'claude:3')], follow2, 'unused')
        self.assertEqual(ok, 'Fine.')
        self.assertIn('repeats the call', chat.call_args.args[0])
        # A different call, or the same call after a user turn, runs normally.
        fresh = self.req.model_copy(update={'messages': [ChatMessage(role='user', content='another')]})
        (_, content, calls), _ = self.chat([Reply(GOOD, 'claude:5')], fresh)
        follow3 = fresh.model_copy(update={'messages': [
            *fresh.messages, ChatMessage(role='assistant', content=content, tool_calls=calls),
            ChatMessage(role='tool', tool_call_id=calls[0]['id'], content='{"answers": []}')]})
        other = block([{"name": "read_file", "arguments": {"path": "a"}}])
        (_, _, other_calls), _ = self.chat([Reply(other, 'claude:6')], follow3, 'unused')
        self.assertEqual(other_calls[0]['function']['name'], 'read_file')

    def test_every_browser_provider_behaves_the_same(self):
        for model in ('claude-web', 'grok-web', 'mistral-web', 'chatgpt-web', 'glm-web'):
            req = self.req.model_copy(update={'model': model})
            with self.subTest(model=model):
                (_, content, calls), chat = self.chat([Reply(BAD, 'x:1')], req)
                self.assertIsNone(calls)
                self.assertTrue(content.startswith(BAD))
                self.assertIn(NOTICE_PREFIX, content)
                self.assertEqual(chat.call_count, 1)
            with self.subTest(model=model, repair=True), patch.object(api, '_FORMAT_REPAIR', True):
                (_, _, calls), chat = self.chat(
                    [Reply(BAD, 'x:1'), Reply(GOOD, 'x:1')], req.model_copy(update={'messages': [
                        ChatMessage(role='user', content='again')]}))
                self.assertEqual(calls[0]['function']['name'], 'ask_user_question')
                self.assertEqual(chat.call_count, 2)

    def test_deepseek_is_unchanged(self):
        fake = Mock()
        fake.chat.return_value = Reply(BAD, 'owned:2')
        req = self.req.model_copy(update={'model': 'deepseek-chat'})
        with patch.object(api, 'get_client', return_value=fake), self.assertRaises(api.ToolCallError):
            api._conversation_tool_chat('p', req, 'default')

    def test_cases_that_must_still_fail(self):
        scenarios = [
            ('claude-web', Reply(BAD, 'claude:1'), 'required'),                    # forced tool choice
            ('claude-web', Reply(BAD, None), None),                               # no conversation id
            ('claude-web', Reply(BAD, 'claude:1', 'length'), None),                # unfinished reply
        ]
        for model, reply, choice in scenarios:
            with self.subTest(model=model, choice=choice, cid=reply.conversation_id, finish=reply.finish_reason):
                req = self.req.model_copy(update={'model': model, 'tool_choice': choice})
                with patch.object(api, '_chat_with_retry', return_value=reply), \
                        self.assertRaises((api.ToolCallError, ConversationStateError)):
                    api._conversation_tool_chat('p', req, None)


if __name__ == '__main__':
    unittest.main()
