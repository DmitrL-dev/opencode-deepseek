// Actual observer in an isolated DOM/fetch fixture. No sockets or browser account.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../browser/opencode-observer.user.js'), 'utf8');
class CustomEvent extends Event { constructor(type, options) { super(type); this.detail = options.detail; } }
async function check(host, requests) {
  const document = new EventTarget(), receipts = [];
  const location = { hostname: host, origin: 'https://' + host, pathname: '/c/expected' };
  let calls = 0;
  const window = { fetch: async () => { calls++; return new Response('answer', { status: 200 }); } };
  document.addEventListener('opencode-local-response-v1', e => receipts.push(JSON.parse(e.detail)));
  vm.runInNewContext(source, { window, document, location, CustomEvent, Request, Response, URL, Uint8Array, DataView, btoa, JSON });
  document.dispatchEvent(new CustomEvent('opencode-local-job-v1', { detail: JSON.stringify({ nonce: 'owned', prompt: 'owned prompt', path: '/c/expected' }) }));
  for (const [endpoint, body] of requests) {
    await window.fetch(location.origin + endpoint, { method: 'POST', body: JSON.stringify(body) });
    await new Promise(resolve => setTimeout(resolve, 15));
  }
  const captured = receipts.filter(r => r.body);
  assert.equal(calls, requests.length, 'observer must not duplicate or suppress site requests');
  assert.equal(captured.length, 1);
  assert.equal(captured[0].request_turn.chat_id, 'expected');
  assert.equal(captured[0].request_turn.prompt, 'owned prompt');
}
(async () => {
  await check('grok.com', [
    ['/rest/app-chat/conversations/other/responses', { message: 'owned prompt' }],
    ['/rest/app-chat/conversations/expected/responses', { message: 'different', history: [{ message: 'owned prompt' }] }],
    ['/rest/app-chat/conversations/new', { message: 'owned prompt' }],
    ['/rest/app-chat/conversations/expected/responses', { message: 'owned prompt' }],
  ]);
  await check('chat.z.ai', [
    ['/api/v2/chat/completions', { chat_id: 'other', messages: [{ role: 'user', content: 'owned prompt' }] }],
    ['/api/v2/chat/completions', { chat_id: 'expected', messages: [{ role: 'user', content: 'owned prompt' }, { role: 'user', content: 'different' }] }],
    ['/api/v2/chat/completions', { chat_id: 'expected', messages: [{ role: 'user', content: 'owned prompt' }] }],
  ]);
  console.log('PASS: exact Grok and GLM request binding, history/neighbor/new-chat rejection, single dispatch');
})().catch(error => { console.error(error); process.exitCode = 1; });
