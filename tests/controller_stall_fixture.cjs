// Run the actual controller's stall path against stale visible DOM, without sockets.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path');
const hostSetTimeout = setTimeout;
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../browser/opencode-controller.user.js'), 'utf8');
const startAt = source.indexOf('    // Sites flagged stalls');
const endAt = source.indexOf('  async function poll()');
if (startAt < 0 || endAt < 0 || endAt <= startAt) {
  console.error('stall section markers missing from opencode-controller.user.js');
  process.exit(2);
}
let section = source.slice(startAt, endAt);
// Old versions defined DOM finishers here. Hoist them only in this fixture to
// reproduce their real runtime availability, then execute the same stall loop.
const helperAt = section.indexOf('  const geminiHtmlToMarkdown');
const helpers = helperAt < 0 ? '' : section.slice(helperAt);
section = helperAt < 0 ? section : section.slice(0, helperAt);
section = section.slice(0, section.lastIndexOf('\n  }'));
// Some CI Node builds expose a non-overridable host performance/setTimeout on
// the VM global. Sync waits plus a real clock then busy-spin until Python's
// 10s kill. Bind through private names so the extracted slice cannot see them.
if (!/\bperformance\.now\(\)/.test(section) || !/\bsetTimeout\b/.test(section)) {
  console.error('stall section no longer uses performance.now/setTimeout');
  process.exit(2);
}
section = section
  .replace(/\bperformance\.now\(\)/g, '__fixtureNow()')
  .replace(/\bawait new Promise\(resolve => setTimeout\(resolve, 100\)\)/g, 'await __fixtureWait()');
(async () => {
  const watchdog = hostSetTimeout(() => {
    console.error('stall fixture watchdog: loop did not finish');
    process.exit(2);
  }, 3000);
  for (const provider of ['mistral', 'claude']) {
    let clock = 0, finished = 0, waits = 0;
    const deadline = 5000;
    const context = vm.createContext({
      site: {provider, stalls: true, path: /^\/chat\//}, dispatching: true,
      pending: {id: 'owned-job', sawCapture: true}, job: {id: 'owned-job', prompt: 'NEW_PROMPT'},
      deadline,
      __fixtureNow: () => clock,
      __fixtureWait: () => {
        if (++waits > 40) throw new Error('stall fixture wait budget exceeded');
        clock += 1000;
        return new Promise(resolve => hostSetTimeout(resolve, 0));
      },
      TextEncoder, btoa,
      location: {pathname: '/chat/owned'},
      document: {querySelector: () => null, querySelectorAll: () => [{
        innerText: 'PREVIOUS_TOOL_CALL', closest: () => null, getAttribute: () => 'false',
      }]},
      finish: async () => {finished++;}, BridgeFailure: Error,
    });
    await assert.rejects(vm.runInContext(`(async () => { ${helpers}\n${section} })()`, context), /observation stalled/);
    assert.equal(finished, 0, 'old DOM must not finish a new request');
    assert.ok(waits > 0, 'stall loop must wait at least once with stale DOM present');
  }
  clearTimeout(watchdog);
  console.log('stale DOM cannot fabricate a terminal completion');
})().catch(error => {console.error(error); process.exitCode = 1;});
