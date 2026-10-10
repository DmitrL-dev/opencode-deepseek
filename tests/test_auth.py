import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx

from deepseek import auth


def session():
    return auth.Session("fake-token", [], "audit-agent", time.time())


class SessionTests(unittest.TestCase):
    def test_only_scoped_deepseek_cookies_are_captured_and_sent(self):
        page = Mock()
        page.url = auth.CHAT_URL
        page.evaluate.side_effect = ["fake-token","audit-agent"]
        context = Mock()
        context.cookies.return_value = [
            {"name":"ds","value":"fake-ds","domain":".deepseek.com","path":"/","secure":True},
            {"name":"foreign","value":"fake-foreign","domain":".unrelated.invalid","path":"/"},
            {"name":"restricted","value":"fake-path","domain":"chat.deepseek.com","path":"/account"},
            {"name":"expired","value":"fake-old","domain":"chat.deepseek.com","path":"/","expires":1},
        ]
        captured = auth._capture_from_context(context, page)
        context.cookies.assert_called_once_with([auth.CHAT_URL])
        with httpx.Client(cookies=captured.cookie_jar()) as client:
            headers = client.build_request("POST","https://chat.deepseek.com/api/v0/chat/completion").headers
            self.assertEqual(headers.get("cookie"), "ds=fake-ds")
            self.assertNotIn("cookie", client.build_request("GET","https://unrelated.invalid/").headers)
            self.assertNotIn("ds=fake-ds", client.build_request("GET","http://chat.deepseek.com/").headers.get("cookie", ""))

    def test_capture_never_reads_storage_on_foreign_or_insecure_origins(self):
        for url in ('https://accounts.google.com/', 'https://chat.deepseek.com.evil.invalid/',
                    'http://chat.deepseek.com/', 'https://chat.deepseek.com:8000/',
                    'https://user@chat.deepseek.com/', 'https://chat.deepseek.com:invalid/'):
            with self.subTest(url=url):
                page, context = Mock(), Mock()
                page.url = url
                self.assertIsNone(auth._capture_from_context(context, page))
                page.evaluate.assert_not_called()
                context.cookies.assert_not_called()

    def test_capture_rejects_invalid_token_without_capturing_cookies(self):
        for token in (None, '', '  ', {}, ['secret'], 123):
            with self.subTest(token=token):
                page, context = Mock(), Mock()
                page.url = auth.CHAT_URL
                page.evaluate.return_value = token
                self.assertIsNone(auth._capture_from_context(context, page))
                context.cookies.assert_not_called()

    def test_cookie_names_are_not_collapsed_across_paths(self):
        captured = session()
        captured.cookies = [
            {"name":"same","value":"root","domain":".deepseek.com","path":"/"},
            {"name":"same","value":"api","domain":".deepseek.com","path":"/api"},
        ]
        with httpx.Client(cookies=captured.cookie_jar()) as client:
            self.assertEqual(client.build_request("GET",auth.CHAT_URL+"api/test").headers["cookie"],
                             "same=api; same=root")

    def test_session_storage_is_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"session"/"session.json"
            previous_umask = os.umask(0o022)
            try:
                saved = session()
                saved.save(path)
            finally:
                os.umask(previous_umask)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            baseline = path.read_bytes()
            saved.token = "fake-new"
            with patch.object(auth.os,"replace",side_effect=OSError("cannot replace")):
                with self.assertRaises(OSError):
                    saved.save(path)
            self.assertEqual(path.read_bytes(), baseline)
            self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(auth.Session.load(path).token,"fake-token")

    def test_legacy_cookie_cache_requires_recapture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"session.json"
            path.write_text(json.dumps({"token":"fake","cookies":{"foreign":"secret"},
                                        "user_agent":"fake","captured_at":time.time()}))
            self.assertIsNone(auth.Session.load(path))
        with self.assertRaises(auth.LoginRequired):
            auth.Session("fake",{},"fake",time.time()).cookie_jar()

    def test_fallback_handles_channel_exceptions_and_saves_requested_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"custom"/"session.json"
            with patch.object(auth,"_headless_refresh",side_effect=[RuntimeError("browser unavailable"),session()]) as refresh:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = auth.get_session(session_file=path,channel="preferred",fallback_channel="fallback",
                                              allow_interactive=False,allow_refresh=True)
            self.assertEqual([call.args[1] for call in refresh.call_args_list],["preferred","fallback"])
            self.assertEqual(auth.Session.load(path).token,result.token)

    def test_failed_headless_login_returns_login_required(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(auth,"_headless_refresh",side_effect=RuntimeError("unavailable")):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(auth.LoginRequired):
                auth.get_session(session_file=Path(directory)/"missing.json",allow_interactive=False,allow_refresh=True)

    def test_profile_capture_is_serialized(self):
        state = {"active":0,"maximum":0}
        barrier = threading.Barrier(3)
        guard = threading.Lock()
        errors = []
        def refresh(*args):
            with guard:
                state["active"] += 1
                state["maximum"] = max(state["maximum"],state["active"])
            time.sleep(0.02)
            with guard:
                state["active"] -= 1
            return session()
        with tempfile.TemporaryDirectory() as directory, patch.object(auth,"_headless_refresh",side_effect=refresh):
            def capture(index):
                try:
                    barrier.wait(timeout=2)
                    auth.get_session(session_file=Path(directory)/str(index)/"session.json",max_age=0,
                                     allow_interactive=False,allow_refresh=True)
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=capture,args=(i,)) for i in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=3)
            self.assertFalse(errors)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(state["maximum"],1)

    def test_environment_is_loaded_before_auth_and_server_config(self):
        code = '''import os,json
from unittest.mock import patch
for key in ["RATE_LIMIT_PER_MINUTE","SERVER_INTERACTIVE_LOGIN","DEEPSEEK_PROFILE_DIR"]:
 os.environ.pop(key,None)
def load(*args,**kwargs):
 os.environ.update(RATE_LIMIT_PER_MINUTE="7",SERVER_INTERACTIVE_LOGIN="0",DEEPSEEK_PROFILE_DIR="/tmp/audit-profile")
with patch("dotenv.load_dotenv",load):
 import server.api as api
 import deepseek.auth as auth
 print(json.dumps([api.RATE_LIMIT_PER_MINUTE,api.SERVER_INTERACTIVE_LOGIN,str(auth.DEFAULT_PROFILE_DIR)]))
'''
        output = subprocess.check_output([sys.executable,"-c",code],cwd=auth.ROOT,text=True)
        self.assertEqual(json.loads(output),[7,False,"/tmp/audit-profile"])


class ManualInspectionTests(unittest.TestCase):
    def test_capture_saves_changed_credentials_once_and_closes_on_interrupt(self):
        first = session()
        later = auth.Session(first.token, [], first.user_agent, first.captured_at + 10)
        rotated = auth.Session('rotated-secret', [], first.user_agent, first.captured_at + 20)
        context, page = Mock(), Mock()
        context.pages = [page]
        page.is_closed.return_value = False
        page.wait_for_timeout.side_effect = [None, None, KeyboardInterrupt]
        output = io.StringIO()
        with patch.object(auth, 'sync_playwright') as playwright, \
                patch.object(auth, '_launch_context', return_value=context) as launch, \
                patch.object(auth, '_safe_goto') as navigate, \
                patch.object(auth, '_capture_from_context', side_effect=[first, later, rotated]), \
                patch.object(auth.Session, 'save', autospec=True) as save, \
                contextlib.redirect_stdout(output):
            auth.keep_open(Path('owned-profile'), Path('owned-session'), channel=None)
        launch.assert_called_once_with(playwright.return_value.__enter__.return_value,
                                       Path('owned-profile'), False, None, allow_fallback=False)
        navigate.assert_called_once_with(page, auth.CHAT_URL)
        self.assertEqual([c.args for c in save.call_args_list],
                         [(first, Path('owned-session')), (rotated, Path('owned-session'))])
        context.close.assert_called_once()
        self.assertNotIn('rotated-secret', output.getvalue())
        self.assertIn('does not confirm account access', output.getvalue())

    def test_window_close_stops_capture_without_recapturing_or_raising(self):
        context, page = Mock(), Mock()
        context.pages = [page]
        page.is_closed.return_value = False

        def close_window(_):
            context.on.call_args.args[1]()
            page.is_closed.return_value = True
            raise auth.PlaywrightError('private-close-detail')

        page.wait_for_timeout.side_effect = close_window
        output = io.StringIO()
        with patch.object(auth, 'sync_playwright'), \
                patch.object(auth, '_launch_context', return_value=context), \
                patch.object(auth, '_safe_goto'), \
                patch.object(auth, '_capture_from_context', return_value=None) as capture, \
                contextlib.redirect_stdout(output):
            auth.keep_open()
        capture.assert_called_once()
        context.close.assert_called_once()
        self.assertNotIn('private-close-detail', output.getvalue())

    def test_inspection_never_falls_back_to_another_browser_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            playwright = Mock()
            launch = playwright.chromium.launch_persistent_context
            launch.side_effect = auth.PlaywrightError('browser unavailable')
            with self.assertRaises(auth.PlaywrightError):
                auth._launch_context(playwright, Path(directory), False, 'chrome', allow_fallback=False)
            launch.assert_called_once()

    def test_cli_inspection_requires_explicit_flag_and_sanitizes_failure(self):
        with patch.object(auth, 'login', return_value=session()) as login, \
                patch.object(auth, 'keep_open') as inspect, contextlib.redirect_stdout(io.StringIO()):
            auth.main([])
            login.assert_called_once_with(channel='chrome')
            inspect.assert_not_called()
            auth.main(['--keep-open', '--channel', 'chromium'])
            inspect.assert_called_once_with(channel=None)
        output = io.StringIO()
        with patch.object(auth, 'keep_open', side_effect=RuntimeError('private-failure-detail')), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as failure:
            auth.main(['--keep-open'])
        self.assertEqual(failure.exception.code, 1)
        self.assertNotIn('private-failure-detail', output.getvalue())
