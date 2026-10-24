"""Installed Harness -> real local bridge -> deterministic provider, offline."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tests.test_tools import block
from integrations.harness import render, yaml_codec
from providers import access
from chat_protocol import Reply
from server import api, config
from server.request_contract import restore


@unittest.skipUnless(os.getenv('RUN_HARNESS_FIXTURES') == '1', 'opt-in installed Harness fixture')
class HarnessReviewCycleTests(unittest.TestCase):
    def test_read_write_partial_round_review_restart_and_acceptance(self):
        self.run_cycle('json')

    def test_dsml_read_write_partial_round_review_restart_and_acceptance(self):
        self.run_cycle('dsml')

    def run_cycle(self, protocol):
        def wire(calls):
            if protocol == 'json':
                return block(calls)
            parts = ['<｜DSML｜ calls>']
            for call in calls:
                parts.append(f'<｜DSML｜ invoke name="{call["name"]}">')
                for name, value in call['arguments'].items():
                    # JSON-valued DSML parameters preserve strings and types exactly.
                    parts.append(f'<｜DSML｜ parameter name="{name}" string="false">'
                                 + json.dumps(value) + '</｜DSML｜ parameter>')
                parts.append('</｜DSML｜ invoke>')
            return '\n'.join([*parts, '</｜DSML｜ calls>'])
        from tests.test_harness_runtime import HarnessRuntimeTests
        case=HarnessRuntimeTests('test_text_uses_one_streamed_request')
        self.addCleanup(case.doCleanups);case.setUp();case.install_shell_safety_fixture()
        library=os.environ['HARNESS_MODULE_ROOT']
        reports=[]
        captures=[]
        original_record=api.record_toolcall_log
        def record(path,direction,content):
            if direction in ('from_agent','request_contract','dispatch_contract'):
                captures.append((direction,content))
            original_record(path,direction,content)

        def chat(prompt,*args):
            attempt=access.current_attempt('deepseek');attempt.dispatch()
            case.prompts.append(prompt);case.conversation_args.append(args)
            step=len(case.prompts);self.assertLessEqual(step,7)
            self.assertTrue(args[2], 'effective thinking must survive the Harness route')
            self.assertNotIn('mark it complete once the objective is achieved',prompt)
            if step==1:
                answer=wire([{'name':'read','arguments':{'file_path':str(case.workspace/'fixture.txt')}}])
            elif step==2:
                self.assertIn('HARNESS_OWNED_FILE_73',prompt)
                answer=wire([{'name':'write','arguments':{'file_path':str(case.workspace/'report.md'),'content':'VERIFIED_FIXTURE\n'}}])
            elif step==3:
                answer=wire([{'name':'read','arguments':{'file_path':str(case.workspace/'report.md')}}])
            elif step==4:
                self.assertIn('VERIFIED_FIXTURE',prompt)
                answer='The owned file is verified; independent review has not yet been requested.'
            elif step==5:
                answer=wire([{'name':'get_goal','arguments':{}}])
            elif step==6:
                result=[m for m in case.calls[-1]['messages'] if m['role']=='tool'][-1]
                value=json.loads(result['content']);goal=value.get('goal',value)
                answer=wire([{'name':'request_goal_review','arguments':{'goal_id':goal['id'],
                    'revision':goal['revision'],'evidence':'Read the fixture, wrote report.md once and read back VERIFIED_FIXTURE.'}}])
            else:
                self.assertIn('Awaiting independent operator review',prompt)
                answer='READY_FOR_OPERATOR_REVIEW: the owned file was written and verified. The whole goal awaits acceptance.'
            attempt.complete();return Reply(answer,f'owned-review:{step*2}')
        case.chat=chat

        def run(session=None,inspect=False):
            doc=yaml_codec().load(case.overlay.read_text())
            doc.extend([{'id':'headless-runner','disabled':True},{'insert':[{
                'id':'owned-review-cycle','name':str(Path(__file__).with_name('harness_review_cycle_fixture.mjs')),
                'config':{'libraryRoot':library,**({'sessionId':session} if session else {}),'inspectComplete':inspect}}]}])
            overlay=case.root/'cycle.patch.yml';overlay.write_bytes(render(doc))
            env={k:os.environ[k] for k in ('PATH','TMPDIR','ELECTRON_RUN_AS_NODE') if k in os.environ}
            env.update(HOME=str(case.root),DSH_HOME=str(case.root/'dsh'),DSH_TELEMETRY_MODE='DISABLED',NO_PROXY='127.0.0.1,::1')
            result=subprocess.run(case.command+['--profile','headless','--patch',str(overlay),'--json','Owned offline goal fixture.'],
                cwd=case.workspace,env=env,text=True,capture_output=True,timeout=35)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            events=[json.loads(s) for s in result.stdout.splitlines() if s.startswith('{')]
            reports.append(events)
            return next(e for e in events if e.get('type')=='fixture/end')

        with patch.object(config,'DEEPSEEK_TOOL_PROTOCOL',protocol), patch.object(config,'DEEPSEEK_TOOL_THINKING',True), patch.object(api,'record_toolcall_log',side_effect=record), \
                patch.object(api,'_FORMAT_REPAIR',False),patch.object(api,'_MAX_CONTINUATIONS',0):
            first=run()
            self.assertEqual(first['reason'],'awaiting_review')
            self.assertEqual(first['goal']['phase'],'active')
            self.assertEqual(first['goal']['activation'],'disarmed')
            self.assertEqual(first['goal']['roundsStarted'],2)
            self.assertEqual(len(case.calls),7)
            output=case.workspace/'report.md';mtime=output.stat().st_mtime_ns
            journal=next((case.root/'dsh/sessions').rglob('session.v4.jsonl*'));prefix=journal.read_bytes()
            ledger=case.guard.path.read_bytes();cache=api.conversation_cache.path.read_bytes()
            accepted=run(first['sessionId'])
            self.assertEqual(accepted['reason'],'operator_accepted')
            self.assertEqual(accepted['goal']['id'],first['goal']['id'])
            self.assertEqual(accepted['goal']['roundsStarted'],first['goal']['roundsStarted'])
            final=run(first['sessionId'],True)
            self.assertEqual(final['reason'],'completion_persisted')
            self.assertEqual(len(case.calls),7,'Operator review and restarts must send no model requests')
            self.assertEqual(case.guard.path.read_bytes(),ledger)
            self.assertEqual(api.conversation_cache.path.read_bytes(),cache)
            self.assertTrue(journal.read_bytes().startswith(prefix))
            self.assertEqual(output.stat().st_mtime_ns,mtime)
            self.assertEqual(output.read_text(),'VERIFIED_FIXTURE\n')
            request=None;verified=0
            for direction,content in captures:
                if direction=='from_agent':request=content
                elif direction=='request_contract':
                    restored=restore(request,content)
                    self.assertTrue(config.effective_thinking(restored));verified+=1
                elif direction=='dispatch_contract':
                    self.assertTrue(content['effective']['thinking'])
                    self.assertEqual(content['effective']['tool_protocol'], protocol)
            self.assertEqual(verified,7)
            calls=[e['event']['data']['name'] for run_events in reports for e in run_events
                   if e.get('type')=='fixture/event' and e['event']['type']=='tool/call']
            self.assertEqual(calls.count('write'),1)
            self.assertEqual(calls.count('read'),2)
            self.assertEqual(calls.count('request_goal_review'),1)
