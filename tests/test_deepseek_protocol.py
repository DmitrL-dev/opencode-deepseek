import threading
import unittest
from unittest.mock import Mock

import httpx

from deepseek.client import DeepSeekClient
from providers.access import AccessGuard, ProviderRejected


class ChatCreationTests(unittest.TestCase):
    def client(self, data):
        calls = []

        def respond(request):
            calls.append(request.url.path)
            return httpx.Response(200, json=data)

        client = DeepSeekClient.__new__(DeepSeekClient)
        client._request_lock = threading.Lock()
        client.access_guard = AccessGuard(path=None, interval=0)
        client._pow_header = Mock(side_effect=AssertionError('must not reach challenge'))
        client._http = httpx.Client(base_url='https://chat.deepseek.com',
                                   transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        return client, calls

    @staticmethod
    def envelope(biz):
        return {'code': 0, 'data': {'biz_code': 0, 'biz_data': biz}}

    def test_accepts_flat_and_nested_chat_identity(self):
        for biz in ({'id': 'fixture-session'},
                    {'chat_session': {'id': 'fixture-session'}},
                    {'id': 'fixture-session', 'chat_session': {'id': 'fixture-session'}}):
            with self.subTest(biz=biz):
                client, calls = self.client(self.envelope(biz))
                self.assertEqual(client.create_chat_session(), 'fixture-session')
                self.assertEqual(calls, ['/api/v0/chat_session/create'])

    def test_invalid_identity_stops_before_challenge_and_blocks_replay(self):
        for biz in ({}, {'id': ''}, {'id': '  '}, {'id': 4}, {'id': True},
                    {'id': 'fixture:2'}, {'id': ' padded '},
                    {'chat_session': None}, {'chat_session': []},
                    {'chat_session': {}}, {'chat_session': {'id': None}},
                    {'id': 'other', 'chat_session': {'id': 'fixture'}},
                    {'id': 'fixture', 'chat_session': None}):
            with self.subTest(biz=biz):
                client, calls = self.client(self.envelope(biz))
                with self.assertRaises(ValueError), self.assertLogs('deepseek.client', level='WARNING'):
                    client.chat('fixture')
                self.assertEqual(client.access_guard.status(), {'deepseek': 'upstream_failure'})
                with self.assertRaises(ProviderRejected):
                    client.chat('must not send')
                self.assertEqual(calls, ['/api/v0/chat_session/create'])
                client._pow_header.assert_not_called()

    def test_business_error_wins_over_any_chat_identity(self):
        for code in (5, 19):
            envelope = self.envelope({'id': 'fixture'})
            envelope['data']['biz_code'] = code
            client, calls = self.client(envelope)
            with self.assertRaises(ProviderRejected) as failure:
                client.create_chat_session()
            self.assertEqual(failure.exception.code,
                             'account_restricted' if code == 5 else 'upstream_failure')
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
