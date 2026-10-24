import assert from 'node:assert/strict';
import { registerReadContext } from '../integrations/harness_read_context.mjs';

const contexts = [];
// No filesystem, tool, session, or provider services are available: registering
// a notice must not perform a read, fake an observation, or mutate history.
const ctx = {systemPrompt: {context: value => contexts.push(value)}};
const first = registerReadContext(ctx);
assert.equal(contexts.length, 1);
const before = contexts[0];
assert.equal(before.required, true);
assert.equal(before.interpolate, false);
assert.equal(typeof before.text, 'string');
assert.match(before.text, new RegExp(first));
assert.match(before.text, /native read tool for that exact file_path/);
assert.match(before.text, /later response/);
assert.match(before.text, /do not repeat it without a reason/);
assert.match(before.text, /does not grant filesystem access/);
const after = registerReadContext(ctx);
assert.notEqual(first, after);
assert.equal(contexts[1].name, before.name);
assert.notEqual(contexts[1].text, before.text);
assert.equal(before.text, contexts[0].text);
assert.throws(() => registerReadContext({}), TypeError);
console.log('read context: unique runtime epoch, stable notice, required context, no side effects');
