import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

import httpx
from deepseek import refusals
from deepseek.client import DeepSeekClient, _biz, _parse_sse
from providers.access import AccessGuard, ProviderRejected


class Refusals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(refusals, 'DIRECTORY', self.root / 'diagnostics')
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_global_codes_numeric_and_string(self):
        for code, reason in refusals.GLOBAL_CODES.items():
            for raw in (code, str(code)):
                with self.subTest(raw=raw):
                    with self.assertRaises(ProviderRejected) as ctx:
                        _biz({'code':raw, 'data': {'biz_code':0}, 'msg':'SECRET'})
                    self.assertEqual(ctx.exception.code, reason)
                    self.assertEqual(ctx.exception.raw_code, code)

    def test_namespaces_are_not_conflated(self):
        self.assertEqual(refusals.classify({'code':5}).code, 'upstream_failure')
        self.assertEqual(refusals.classify({'code':5}, completion=True).code, 'account_muted')
        self.assertEqual(refusals.classify({'code':10}, completion=True).code, 'upstream_failure')
        with self.assertRaises(ProviderRejected) as ctx:
            _biz({'code':0,'data':{'biz_code':5}}, completion=True)
        self.assertEqual(ctx.exception.code, 'account_muted')
        self.assertEqual((ctx.exception.raw_code,ctx.exception.business_code),(0,5))

    def test_stream_error_codes_and_nested_envelope(self):
        for data, reason in [({'code':5},'account_muted'),({'code':50006},'account_muted'),
                             ({'v':{'code':40012}},'account_banned'),
                             ({'code':0,'data':{'biz_code':5}},'account_muted')]:
            with self.subTest(data=data), self.assertRaises(ProviderRejected) as ctx:
                list(_parse_sse(['event: error','data: '+json.dumps(data),''],{}))
            self.assertEqual(ctx.exception.code,reason)

    def test_gateway_takes_precedence_over_status_and_body(self):
        for status, headers in [(202,{'x-amzn-waf-action':'challenge'}),
                                (405,{'x-amzn-waf-action':'captcha'}),
                                (429,{'cf-mitigated':'challenge'}),
                                (403,{'cf-mitigated':'challenge'})]:
            with self.subTest(status=status):
                response=httpx.Response(status,headers=headers,json={'code':40003})
                with self.assertRaises(ProviderRejected) as ctx:
                    refusals.check_response(response,'challenge')
                self.assertEqual(ctx.exception.code,'gateway_challenge')
                self.assertEqual(ctx.exception.http_status,status)
        with self.assertRaises(ProviderRejected) as ctx:
            refusals.check_response(httpx.Response(429),'challenge')
        self.assertEqual(ctx.exception.code,'quota_exceeded')

    def test_unexpected_redirect_is_terminal(self):
        with self.assertRaises(ProviderRejected) as ctx:
            refusals.check_response(httpx.Response(302, headers={'location':'https://offline.invalid'}),'challenge')
        self.assertEqual(ctx.exception.code, 'upstream_failure')
        self.assertEqual(ctx.exception.http_status, 302)

    def test_unknown_codes_preserved_but_never_grant_auth_recovery(self):
        guard=AccessGuard(self.root/'guard.json',interval=0)
        with self.assertRaises(ProviderRejected):
            with guard.attempt('deepseek') as attempt:
                attempt.dispatch()
                DeepSeekClient._pre_generation_biz(httpx.Response(200,json={'code':98765,'msg':'SECRET'}),attempt)
        state=json.loads(guard.path.read_text())['deepseek']
        self.assertNotIn('recoverable_auth',state)
        record=json.loads(next(refusals.DIRECTORY.glob('*.json')).read_text())
        self.assertEqual(record['api_code'],98765)
        self.assertEqual(record['phase'],'challenge')
        self.assertEqual(record['attempt_id'],attempt.id)
        before=guard.path.read_bytes()
        with self.assertRaises(ProviderRejected):guard.begin('deepseek')
        self.assertEqual(guard.path.read_bytes(),before)

    def test_restriction_promotes_auth_pause_and_removes_recovery_evidence(self):
        guard=AccessGuard(self.root/'guard.json',interval=0)
        state={'deepseek':{'pause':'session_expired','pending':'a'*32,'dispatched':True,
                          'recoverable_auth':True,'usage':[[123,'http']]}}
        with guard._state() as current:current.update(state);guard._save(current)
        guard.reject('deepseek',ProviderRejected('account_muted'))
        actual=json.loads(guard.path.read_text())['deepseek']
        self.assertEqual(actual['pause'],'account_muted');self.assertNotIn('recoverable_auth',actual)
        self.assertEqual(actual['usage'],state['deepseek']['usage'])
        self.assertEqual(actual['pending'],'a'*32)
        guard.reject('deepseek',ProviderRejected('session_expired'))
        self.assertEqual(json.loads(guard.path.read_text())['deepseek'],actual)

    def test_private_metadata_has_no_response_text_or_secret(self):
        error=refusals.classify({'code':50006,'msg':'SECRET','data':{'token':'SECRET','biz_code':123}})
        refusals.save(error,phase='challenge',status=200)
        refusals.save(error,phase='challenge',status=200)
        paths=list(refusals.DIRECTORY.glob('*.json'));self.assertEqual(len(paths),1)
        text=paths[0].read_text();self.assertNotIn('SECRET',text)
        record=json.loads(text)
        self.assertEqual(set(record),{'version','at','reason','phase','http_status','api_code','biz_code','gateway','attempt_id'})
        if os.name!='nt':self.assertEqual(paths[0].stat().st_mode & 0o777,0o600)

    def test_full_directory_does_not_delete_evidence(self):
        with patch.object(refusals,'MAX_FILES',1):
            refusals.save(ProviderRejected('account_muted'),phase='completion')
            first=next(refusals.DIRECTORY.iterdir());before=first.read_bytes()
            refusals.save(ProviderRejected('account_banned'),phase='completion')
            self.assertEqual(len(list(refusals.DIRECTORY.iterdir())),1)
            self.assertEqual(first.read_bytes(),before)

    def test_symlink_directory_is_not_followed(self):
        target=self.root/'target';target.mkdir()
        try:refusals.DIRECTORY.symlink_to(target,target_is_directory=True)
        except OSError:self.skipTest('symlinks unavailable')
        refusals.save(ProviderRejected('account_muted'),phase='completion')
        self.assertEqual(list(target.iterdir()),[])

    def test_numeric_fields_reject_bool_and_unbounded_values(self):
        for value in (True,1.5,{},'SECRET','9'*100):self.assertIsNone(refusals.number(value))

    def test_complete_client_refusal_diagnostics_and_no_replay(self):
        for phase, path in [('create_chat','/api/v0/chat_session/create'),
                            ('challenge','/api/v0/chat/create_pow_challenge'),
                            ('completion','/api/v0/chat/completion')]:
            for code in (50006,40012,40029,40003):
                calls=[]
                def handler(request):
                    calls.append(request.url.path)
                    if request.url.path==path:return httpx.Response(200,json={'code':code,'msg':'SECRET'})
                    value={'chat_session':{'id':'fixture'}} if request.url.path.endswith('/create') else {'challenge':{}}
                    return httpx.Response(200,json={'code':0,'data':{'biz_code':0,'biz_data':value}})
                client=object.__new__(DeepSeekClient)
                client._request_lock=threading.Lock();client._pow_lock=threading.Lock()
                client._pow=Mock();client._pow.make_header.return_value='fixture'
                client.access_guard=AccessGuard(self.root/(phase+str(code)+'.json'),interval=0)
                client._http=httpx.Client(base_url='https://offline.invalid',transport=httpx.MockTransport(handler))
                self.addCleanup(client.close)
                before=set(refusals.DIRECTORY.glob('*.json'))
                with self.subTest(phase=phase,code=code):
                    with self.assertRaises(ProviderRejected) as ctx:client.chat('fixture')
                    self.assertEqual(ctx.exception.code,refusals.GLOBAL_CODES[code])
                    entries=set(refusals.DIRECTORY.glob('*.json'))-before
                    self.assertEqual(len(entries),1)
                    meta=json.loads(entries.pop().read_text())
                    self.assertEqual((meta['phase'],meta['http_status'],meta['api_code']),(phase,200,code))
                    with self.assertRaises(ProviderRejected):client.chat('do not send')
                    self.assertEqual(calls.count(path),1)
