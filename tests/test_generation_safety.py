"""Offline regressions for generation pacing and expiring PoW authorization."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from deepseek.pow import DeepSeekPow, PowHeader
from providers.access import AccessGuard, ProviderRejected
from tests.test_api import mock_client, snapshot


class GenerationSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'guard.json'
        self.clock = [100000.0]
        for p in (patch('providers.access.time.time', side_effect=lambda: self.clock[0]),
                  patch('providers.access.time.monotonic', side_effect=lambda: self.clock[0]),
                  patch('providers.access.time.sleep', side_effect=self.sleep)):
            p.start(); self.addCleanup(p.stop)
        self.guard = AccessGuard(self.path, interval=30)

    def sleep(self, delay):
        self.clock[0] += delay

    def state(self):
        return json.loads(self.path.read_text())['deepseek']

    def test_generations_have_their_own_minimum_interval(self):
        with self.guard.attempt('deepseek') as first:
            first.dispatch()
            self.clock[0] += 20
            first.dispatch(kind='generation')
            sent = self.clock[0]
        with AccessGuard(self.path, interval=30).attempt('deepseek') as second:
            second.dispatch()
            second.dispatch(kind='generation')
        self.assertGreaterEqual(self.clock[0] - sent, 30)
        self.assertEqual([kind for _, kind in self.state()['usage']], ['http', 'generation', 'http', 'generation'])

    def test_preflight_waits_before_a_fresh_challenge_without_charging(self):
        with self.guard.attempt('deepseek') as first:
            first.dispatch(kind='generation')
        before = self.state()['usage']
        with self.guard.attempt('deepseek') as second:
            second.preflight_generation(False)
            self.assertGreaterEqual(self.clock[0], before[-1][0] + 30)
            self.assertEqual(self.state()['usage'], before)
            self.assertFalse(second.dispatched)

    def test_legacy_ledger_imposes_generation_spacing_without_rewriting_it(self):
        with self.guard._state() as state:
            state['deepseek'] = {'usage': [[self.clock[0], 'generation']]}
            self.guard._save(state)
        with self.guard.attempt('deepseek') as attempt:
            before = self.path.read_bytes()
            attempt.preflight_generation(False)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertGreaterEqual(self.clock[0], 100030)

    def test_budget_reset_and_manual_resume_preserve_generation_pacing(self):
        with self.guard.attempt('deepseek') as first:
            first.dispatch(kind='generation')
        deadline = self.state()['next_generation_at']
        self.guard.reset_budget(self.guard.budget_status()['revision'])
        self.guard.resume('deepseek')
        self.assertEqual(self.state()['next_generation_at'], deadline)
        with self.guard.attempt('deepseek') as second:
            second.dispatch(kind='generation')
        self.assertGreaterEqual(self.state()['usage'][0][0], deadline)

    def test_generation_deadline_must_be_finite_and_nonnegative(self):
        for bad in [True, -1, float('nan'), float('inf'), 'tomorrow']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AccessGuard._decode({'deepseek': {'next_generation_at': bad}})

    @staticmethod
    def challenge(**overrides):
        return dict(algorithm='DeepSeekHashV1', salt='fixture', expire_at=123456,
                    expire_after=10000, challenge='fixture', difficulty=1,
                    signature='fixture', target_path='/api/v0/chat/completion', **overrides)

    def solver(self):
        solver = DeepSeekPow.__new__(DeepSeekPow)
        solver.solve = Mock(return_value=1)
        return solver

    def test_expiry_before_solving_or_during_solving_never_yields_an_answer(self):
        solver = self.solver()
        with self.assertRaises(ProviderRejected) as error:
            solver.make_header(self.challenge(), requested_at=self.clock[0] - 11)
        self.assertEqual(error.exception.code, 'local_pow_expired')
        solver.solve.assert_not_called()
        solver.solve.side_effect = lambda *args: self.sleep(11) or 1
        with self.assertRaises(ProviderRejected):
            solver.make_header(self.challenge())
        solver.solve.assert_called_once()

    def test_challenge_http_latency_counts_against_pow_lifetime(self):
        solver = self.solver()
        requested = self.clock[0]
        self.clock[0] += 8
        answer = solver.make_header(self.challenge(), requested_at=requested)
        answer.validate()
        self.clock[0] += 2
        with self.assertRaises(ProviderRejected):
            answer.validate()
        payload = json.loads(base64.b64decode(answer))
        self.assertEqual(payload['answer'], 1)
        self.assertNotIn('deadline', payload)

    def test_malformed_pow_lifetimes_are_refused_without_solving(self):
        for lifetime in [None, True, '1000', 0, -1, float('nan'), float('inf')]:
            solver = self.solver(); challenge = self.challenge(); challenge['expire_after'] = lifetime
            with self.subTest(lifetime=lifetime), self.assertRaises(ValueError):
                solver.make_header(challenge)
            solver.solve.assert_not_called()

    def test_pow_expiring_while_dispatch_waits_preserves_challenge_charge(self):
        with self.assertRaises(ProviderRejected) as error:
            with self.guard.attempt('deepseek') as attempt:
                attempt.dispatch()
                answer = PowHeader('fixture', self.clock[0] + 1)
                attempt.dispatch(kind='generation', validate=answer.validate)
        self.assertEqual(error.exception.code, 'local_pow_expired')
        state = self.state()
        self.assertEqual([k for _, k in state['usage']], ['http'])
        self.assertEqual(state['pause'], 'local_pow_expired')
        self.assertNotIn('next_generation_at', state)

    def test_pow_expiring_during_fsync_retracts_only_the_unsent_generation(self):
        with self.guard.attempt('deepseek') as attempt:
            attempt.dispatch()
            self.clock[0] += 3
            before = self.state()
            answer = PowHeader('fixture', self.clock[0] + 1)
            save = self.guard._save
            def late_save(state):
                save(state)
                if any(kind == 'generation' for _, kind in state['deepseek'].get('usage', [])):
                    self.clock[0] += 2
            with patch.object(self.guard, '_save', side_effect=late_save), self.assertRaises(ProviderRejected):
                attempt.dispatch(kind='generation', validate=answer.validate)
            after = self.state()
            self.assertEqual(after, before)
            self.assertFalse(attempt.generation_dispatched)

    def test_expired_header_cannot_reach_the_completion_transport(self):
        client = mock_client(snapshot('not sent', 'FINISHED'))
        self.addCleanup(client.close)
        client.access_guard = AccessGuard(self.path, interval=0)
        client._pow_header = Mock(return_value=PowHeader('fixture', self.clock[0] - 1))
        with patch.object(client._http, 'send', wraps=client._http.send) as send:
            with self.assertRaises(ProviderRejected):
                client.chat('offline fixture', conversation_id='existing:2')
        send.assert_not_called()
        self.assertEqual(self.state().get('usage', []), [])
