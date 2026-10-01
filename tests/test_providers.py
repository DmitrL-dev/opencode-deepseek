import asyncio
import base64
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers.antigravity import AntigravityClient, parse_result, parse_stream
from providers.browser_protocol import connect_completed, glm_answer, grok_answer, mistral_answer
from providers.common import ProviderUnavailable, completion_timeout
from providers.conversations import decode, encode

UUID = "12345678-1234-4234-8234-123456789abc"


def envelope(flags, payload):
    return bytes([flags]) + struct.pack(">I", len(payload)) + payload


def sse(obj):
    return ("data: " + json.dumps(obj) + "\n\n").encode()


def cli_init(model="flash"):
    return {"event":"init", "init":{"tools":[], "agent":"opencode-text-bridge",
            "model":model, "permission_mode":"request-review"}}


class ProviderTests(unittest.TestCase):
    def test_model_cannot_be_a_cli_flag_before_process_launch(self):
        with patch("providers.antigravity.cli_path") as binary:
            for model in ("--dangerously-skip-permissions", "flash\n--agent", "flash pro"):
                with self.assertRaises(ValueError):
                    AntigravityClient().chat("marker",model=model)
            binary.assert_not_called()

    def test_conversation_tokens_are_scoped_and_paths_cannot_escape(self):
        for provider, reference in (("gemini", UUID), ("grok", "/c/" + UUID),
                                    ("glm", "/c/" + UUID), ("mistral", "/chat/" + UUID),
                                    ("kimi", "/chat/" + UUID)):
            token = encode(provider, "default", reference)
            self.assertEqual(decode(token, provider), ("default", reference))
            for other in {"gemini", "grok", "glm", "mistral", "kimi"} - {provider}:
                with self.assertRaises(ValueError):
                    decode(token, other)
        for path in ("https://evil.test/", "//evil.test/", "/c/../auth", "/c/a?redirect=evil", "/c/a%2fb"):
            with self.assertRaises(ValueError):
                encode("grok", "default", path)

    def test_malformed_tokens_cannot_supply_a_reference(self):
        for value in ("web:grok:!", "web:grok:" + "a" * 1025,
                      "web:grok:" + base64.urlsafe_b64encode(b'{"url":"https://evil"}').decode()):
            with self.assertRaises(ValueError):
                decode(value, "grok")

    def test_grok_partial_or_error_tail_cannot_succeed(self):
        final = {"result": {"response": {"modelResponse": {"message": "answer", "partial": False}}}}
        body = json.dumps(final).encode()
        self.assertEqual(grok_answer(body), "answer")
        for bad in (json.dumps({"result": {"response": {"token": "partial"}}}).encode(),
                    body + b'\n{"error":"quota"}', body + b"\n" + body,
                    body.replace(b'false', b'true')):
            with self.assertRaises(ProviderUnavailable):
                grok_answer(bad)

    def test_grok_resumed_shape_and_nested_errors(self):
        model = {"message":"answer", "partial":False}
        for wrapper in (lambda value: {"result":{"response":{"modelResponse":value}}},
                        lambda value: {"result":{"modelResponse":value}}):
            self.assertEqual(grok_answer(json.dumps(wrapper(model)).encode()), "answer")
            for error in ({"error":True}, {"streamErrors":[{"error":"fatal"}]}):
                with self.assertRaises(ProviderUnavailable):
                    grok_answer(json.dumps(wrapper({**model, **error})).encode())

    def test_glm_requires_completion_and_excludes_reasoning(self):
        body = (sse({"type":"chat:completion","data":{"phase":"thinking","delta_content":"secret reasoning"}})
                + sse({"type":"chat:completion","data":{"phase":"answer","delta_content":"answer"}}))
        with self.assertRaises(ProviderUnavailable):
            glm_answer(body + b"data: [DONE]\n\n")
        finished = body + sse({"type":"chat:completion","data":{"done":True}})
        self.assertEqual(glm_answer(finished), "answer")
        with self.assertRaises(ProviderUnavailable):
            glm_answer(finished + sse({"error":"failed"}))

    def test_connect_needs_a_successful_end_envelope(self):
        message = envelope(0, b"\x0a\x06answer")
        end = envelope(2, b'{"metadata":{}}')
        connect_completed(message + end)
        for bad in (message, message + end[:-1], message + envelope(2, b'{"error":{"code":"unauthenticated"}}'),
                    message + end + message, envelope(1, b"compressed") + end, end):
            with self.assertRaises(ProviderUnavailable):
                connect_completed(bad)

    def test_glm_reasoning_completion_cannot_finalize_an_answer(self):
        answer = sse({"type":"chat:completion","data":{"phase":"answer","content":"partial"}})
        reasoning_end = sse({"type":"chat:completion","data":{"phase":"thinking","done":True}})
        complete = sse({"type":"chat:completion","data":{"phase":"answer","done":True}})
        for body in (answer + reasoning_end, reasoning_end + answer, answer + complete + answer):
            with self.assertRaises(ProviderUnavailable):
                glm_answer(body)

    def test_glm_v2_global_terminal_confirms_only_the_main_answer(self):
        answer = sse({"type":"chat:completion","data":{"phase":"answer","delta_content":"answer"}})
        terminal = sse({"type":"chat:completion","data":{"phase":"done","done":True}})
        subagent = sse({"type":"chat:completion","data":{"scope":"subagent","phase":"done","done":True}})
        self.assertEqual(glm_answer(answer + terminal), "answer")
        for body in (terminal, answer + subagent, answer + terminal + answer):
            with self.assertRaises(ProviderUnavailable):
                glm_answer(body)

    def test_mistral_missing_terminal_and_errors_are_rejected(self):
        body = sse({"choices":[{"delta":{"content":"answer"},"finish_reason":None}]})
        with self.assertRaises(ProviderUnavailable):
            mistral_answer(body)
        finished = body + sse({"choices":[{"delta":{},"finish_reason":"stop"}]})
        self.assertEqual(mistral_answer(finished), "answer")
        with self.assertRaises(ProviderUnavailable):
            mistral_answer(finished + sse({"error":"quota"}))

    def test_mistral_never_appends_text_after_terminal_completion(self):
        text = sse({"choices":[{"delta":{"content":"answer"},"finish_reason":None}]})
        stop = sse({"choices":[{"delta":{},"finish_reason":"stop"}]})
        delta = sse({"type":"message.delta","text":"late"})
        complete = sse({"type":"message.completed"})
        self.assertEqual(mistral_answer(text + stop + b'data: [DONE]\n\n'), "answer")
        for body in (text + stop + text, text + complete + delta,
                     text + complete + complete, text + b'data: [DONE]\n\n' + delta):
            with self.assertRaises(ProviderUnavailable):
                mistral_answer(body)

    def test_nonfinite_or_unbounded_timeout_is_rejected(self):
        for value in ("nan", "inf", "0", "-1", "1801"):
            with patch.dict(os.environ, {"WEB_PROVIDER_TIMEOUT": value}), self.assertRaises(ValueError):
                completion_timeout()

    def test_cli_only_accepts_explicit_success_with_valid_conversation(self):
        obj = {"status":"SUCCESS","response":"answer","conversation_id":UUID}
        self.assertEqual(parse_result(json.dumps(obj), 0, "flash").text, "answer")
        for changes, code in (({"status":"WAITING"}, 0), ({"error":"quota"}, 0),
                              ({"response":42}, 0), ({"conversation_id":"../private"}, 0), ({}, 1)):
            with self.assertRaises(ProviderUnavailable):
                parse_result(json.dumps({**obj, **changes}), code, "flash")
        with self.assertRaises(ProviderUnavailable):
            parse_result(json.dumps(obj), 0, "flash", "another-conversation")

    def test_cli_prompt_is_stdin_and_workspace_has_no_user_project(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "agy-test"
            script.write_text("#!" + sys.executable + "\n" +
                              "import sys,json,pathlib\n"
                              "print(" + repr(json.dumps(cli_init())) + ",flush=True)\n"
                              "p=json.loads(sys.stdin.read())['message']['content']\n"
                              "assert p=='bridge marker' and p not in sys.argv\n"
                              "assert '--disable-slash-commands' in sys.argv\n"
                              "assert sys.argv[sys.argv.index('--print-timeout')+1].endswith('s')\n"
                              "assert '--dangerously-skip-permissions' not in sys.argv\n"
                              "assert list(pathlib.Path('.').iterdir())==[pathlib.Path('.agents')]\n"
                              "assert 'tools: []' in pathlib.Path('.agents/agents/opencode-text-bridge.md').read_text()\n"
                              "print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':p,'conversation_id':'" + UUID + "'}}))\n")
            script.chmod(0o700)
            with patch.dict(os.environ, {"ANTIGRAVITY_BIN": str(script)}):
                reply = AntigravityClient().chat("bridge marker", model="flash")
            self.assertEqual(reply.text, "bridge marker")

    def test_cli_blocked_stdin_can_be_cancelled_and_process_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "agy-test"
            pidfile = root / "pid"
            script.write_text("#!" + sys.executable + "\nimport os,time,pathlib,sys\n"
                              "print(" + repr(json.dumps(cli_init())) + ",flush=True)\n"
                              "sys.stdin.read(1)\n"
                              "pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()))\n"
                              "time.sleep(30)\n")
            script.chmod(0o700)

            def cancelled():
                if pidfile.exists():
                    raise asyncio.CancelledError()

            with patch.dict(os.environ, {"ANTIGRAVITY_BIN":str(script)}), self.assertRaises(asyncio.CancelledError):
                AntigravityClient(cancelled).chat("x" * 1024 * 1024, model="flash")
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    @unittest.skipUnless(os.name == 'posix', 'Owned process-group fixture')
    def test_cli_descendant_ignoring_term_does_not_survive_success_cancel_or_timeout(self):
        import signal
        import subprocess
        for mode in ('success','cancel','timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pidfile = root / 'child-pid'
                child = "import os,signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));time.sleep(30)"
                script = root / 'agy-test'
                script.write_text('#!' + sys.executable + '\nimport sys,json,time,subprocess,pathlib\n'
                    + 'subprocess.Popen([sys.executable,"-c",' + repr(child) + '])\n'
                    + 'while not pathlib.Path(' + repr(str(pidfile)) + ').exists(): time.sleep(.01)\n'
                    + 'print(' + repr(json.dumps(cli_init())) + ',flush=True)\n'
                    + 'sys.stdin.readline()\n'
                    + ('print(' + repr(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'answer','conversation_id':UUID}})) + ',flush=True)\n' if mode == 'success' else 'time.sleep(30)\n'))
                script.chmod(0o700)
                def cancelled():
                    if mode == 'cancel' and pidfile.exists():
                        raise asyncio.CancelledError()
                try:
                    with patch.dict(os.environ, {'ANTIGRAVITY_BIN':str(script),'WEB_PROVIDER_TIMEOUT':'2'}):
                        if mode == 'success':
                            self.assertEqual(AntigravityClient(cancelled).chat('marker',model='flash').text,'answer')
                        else:
                            with self.assertRaises(asyncio.CancelledError if mode == 'cancel' else ProviderUnavailable):
                                AntigravityClient(cancelled).chat('marker',model='flash')
                    pid = int(pidfile.read_text())
                    state = subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
                    self.assertTrue(not state or state.startswith('Z'), 'Owned descendant remains runnable: ' + state)
                finally:
                    if pidfile.exists():
                        try:
                            os.kill(int(pidfile.read_text()),signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_cli_rejects_tool_capabilities_before_sending_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "agy-test"
            received = Path(directory) / "received"
            frame = cli_init()
            frame['init']['tools'] = ['run_command']
            script.write_text("#!" + sys.executable + "\nimport sys,pathlib\n"
                              "print(" + repr(json.dumps(frame)) + ",flush=True)\n"
                              "pathlib.Path(" + repr(str(received)) + ").write_text(sys.stdin.read())\n")
            script.chmod(0o700)
            with patch.dict(os.environ, {"ANTIGRAVITY_BIN":str(script)}), self.assertRaises(ProviderUnavailable):
                AntigravityClient().chat("private prompt", model="flash")
            self.assertFalse(received.exists() and received.read_text())

    def test_cli_stream_needs_verified_init_and_exactly_one_terminal_result(self):
        result = {"event":"result","result":{"status":"SUCCESS","response":"answer","conversation_id":UUID}}
        body = json.dumps(cli_init()) + '\n' + json.dumps(result)
        self.assertEqual(parse_stream(body, 0, "flash").text, "answer")
        tool = {"event":"step_update","step_update":{"step_type":"tool","tool_name":"run_command"}}
        for bad in (json.dumps(result), body+'\n'+json.dumps(result),
                    json.dumps(cli_init())+'\n'+json.dumps(tool)+'\n'+json.dumps(result)):
            with self.assertRaises(ProviderUnavailable):
                parse_stream(bad, 0, "flash")
