"""Opt-in real browser fixtures; all origins are intercepted, no live accounts."""

import base64
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from providers.browser_protocol import glm_answer

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv("RUN_BROWSER_FIXTURES") == "1", "Opt-in isolated browser fixtures")
class UserscriptFixtureTests(unittest.TestCase):
    def fixture(self, draft="", unrelated=False):
        prompt = "User:\nReply ONLY with FIXTURE_OK"
        job = {"id":"fixture-job", "provider":"glm", "prompt":prompt,
               "path":"/c/fixture", "lease":"fixture-lease"}
        body = ('data: ' + json.dumps({"type":"chat:completion","data":{"phase":"answer","content":"FIXTURE_OK","done":True}}) + '\n\n').encode()
        results, upstream = [], []
        store = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context()

                def route(request_route):
                    request = request_route.request
                    if request.method == "POST":
                        upstream.append(request.post_data)
                        if urlsplit(request.url).path == "/api/chats/new":
                            request_route.fulfill(status=200,content_type="application/json",body=b'{"id":"created"}')
                        else:
                            request_route.fulfill(status=200,content_type="text/event-stream",body=body)
                    else:
                        # An owned fixture with a real textarea and send handler.
                        html = '''<textarea id="chat-input"></textarea><script>
                          const editor = document.getElementById('chat-input');
                          editor.addEventListener('keydown', async event => {
                            if (event.key !== 'Enter') return;
                            /* unrelated-request */
                            const response = await fetch('/api/chat/completions', {
                              method:'POST', headers:{'Content-Type':'application/json'},
                              body:JSON.stringify({message:editor.value})
                            });
                            window.frontendReceivedResponse = true;
                            await response.text();
                          });
                        </script>'''
                        if unrelated:
                            html = html.replace('/* unrelated-request */', "await fetch('/api/chats/new', {method:'POST', body:JSON.stringify({message:editor.value})});")
                        request_route.fulfill(status=200,content_type="text/html",body=html)

                context.route("**/*", route)

                def rpc(_, value):
                    path = urlsplit(value["url"]).path
                    if path.startswith("/browser/jobs/"):
                        payload = {"job":None if results else job}
                    elif path == "/browser/result":
                        results.append(json.loads(value["data"]))
                        payload = {"ok":True}
                    else:
                        raise AssertionError("Unexpected local request")
                    self.assertEqual(value["headers"]["Authorization"], "Bearer " + "x" * 43)
                    return {"status":200,"responseText":json.dumps(payload)}

                context.expose_binding("fixtureRpc", rpc)
                context.expose_binding("fixtureGet", lambda _, key, default:store.get(key,default))
                context.expose_binding("fixtureSet", lambda _, key, value:store.update({key:value}))
                context.add_init_script('''
                    window.fixtureRoots = [];
                    const nativeShadow = HTMLElement.prototype.attachShadow;
                    HTMLElement.prototype.attachShadow = function(options) {
                      const root = nativeShadow.call(this, options);
                      window.fixtureRoots.push(root); return root;
                    };
                    window.GM = {xmlHttpRequest:window.fixtureRpc,getValue:window.fixtureGet,setValue:window.fixtureSet};
                ''')
                context.add_init_script((ROOT/"browser/opencode-observer.user.js").read_text())
                page = context.new_page()
                page.goto("https://chat.z.ai/c/fixture")
                if draft:
                    page.locator("#chat-input").fill(draft)
                controller = (ROOT/"browser/opencode-controller.user.js").read_text().replace("__BRIDGE_TOKEN__","x"*43,1)
                page.add_script_tag(content=controller)
                page.wait_for_function("window.fixtureRoots.length > 0 && window.fixtureRoots[0].querySelector('button')")
                page.evaluate("window.fixtureRoots[0].querySelector('button').click()")
                # A binding resolves only once the complete result has arrived.
                page.wait_for_function("document.querySelector('div').style.position === 'fixed'")
                deadline = __import__("time").monotonic() + 5
                while not results and __import__("time").monotonic() < deadline:
                    page.wait_for_timeout(50)
                self.assertEqual(len(results),1)
                return results[0], upstream, page.locator("#chat-input").input_value(), prompt
            finally:
                browser.close()

    def test_site_send_handler_and_response_capture_finish_one_job(self):
        result, requests, _, prompt = self.fixture()
        self.assertEqual(len(requests),1)
        self.assertEqual(json.loads(requests[0])["message"],prompt)
        self.assertEqual(result["result"]["path"],"/c/fixture")
        self.assertEqual(glm_answer(base64.b64decode(result["result"]["body"])),"FIXTURE_OK")

    def test_existing_draft_is_preserved_and_no_prompt_is_sent(self):
        draft = "unsent fixture draft"
        result, requests, value, _ = self.fixture(draft)
        self.assertEqual(requests,[])
        self.assertEqual(value,draft)
        self.assertIn("error",result["result"])

    def test_noncompletion_post_with_the_prompt_cannot_finish_the_job(self):
        result, requests, _, _ = self.fixture(unrelated=True)
        self.assertEqual(len(requests),2)
        self.assertEqual(glm_answer(base64.b64decode(result["result"]["body"])),"FIXTURE_OK")
