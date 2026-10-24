import assert from 'node:assert/strict';
import { registerGoalCompletionControl } from '../integrations/harness_goal_completion.mjs';

let guard, context, onEvent, completed = 0, reviewTool;
const commands = new Map();
const a = {status: 'idle', session: {}, goal: {id: 'whole', revision: 18,
  objective: 'Implement every plan, including all platform acceptance.', phase: 'active', roundsStarted: 15},
  history: ['old'], counters: [62, 485]};
const b = {...a, session: {}, goal: {...a.goal}};
const ctx = {tools: {guard: fn => {guard = fn;}, register: tool => {reviewTool = tool;}}, systemPrompt: {context: c => {context = c;}},
  on: (event, fn) => {assert.equal(event, 'session/event'); onEvent = fn;},
  commands: {register: c => commands.set(c.name, c)},
  goals: {get: agent => ({...agent.goal}), disarm: agent => {agent.activation = 'disarmed';},
    complete: (agent, ref) => {assert.equal(ref.id, agent.goal.id); assert.equal(ref.revision, agent.goal.revision);
      agent.goal = {...agent.goal, phase: 'complete', revision: ref.revision + 1}; completed++;}}};
registerGoalCompletionControl(ctx);
const call = (name, agent = a, rawInput = '') => commands.get(name).handler({agent, rawInput});
const review = async () => {
  const result = await call('bridge-goal-review');
  assert.equal(result.kind, 'success');
  assert.ok(result.text.includes(a.goal.objective));
  return result.text.split('/bridge-goal-complete ')[1];
};
const blocked = () => guard({name: 'update_goal', agent: a,
  arguments: {goal_id: 'whole', revision: 18, action: 'complete'}});
assert.match(blocked(), /GOAL_COMPLETION_REVIEW_REQUIRED/);
assert.match(blocked(), /goal is unchanged/);
assert.equal(a.goal.phase, 'active');
for (const name of ['read', 'edit', 'bash', 'get_goal', 'create_goal']) assert.equal(guard({name}), undefined);
for (const action of ['edit', 'pause', 'resume', 'blocked']) assert.equal(guard({name: 'update_goal', arguments: {action}}), undefined);
assert.equal(context.required, true);
assert.match(context.text, /every acceptance criterion/);
assert.equal(reviewTool.name, 'request_goal_review');
assert.equal(reviewTool.parameters.additionalProperties, false);
const ready = {goal_id: 'whole', revision: 18, evidence: 'All criteria and their verification evidence.'};
for (const args of [{...ready, revision: 17}, {...ready, goal_id: 'another'},
    {...ready, revision: 18.5}, {...ready, evidence: ' '}, {...ready, approve: true}]) {
  await assert.rejects(reviewTool.execute(args, {agent: a}));
}
assert.equal(completed, 0);
const receipt = await reviewTool.execute(ready, {agent: a});
assert.match(receipt, /Awaiting independent operator review/);
assert.equal(a.activation, 'disarmed');
assert.equal(a.goal.phase, 'active');
assert.equal(a.goal.revision, 18);
assert.equal((await call('bridge-goal-complete', a, 'forged')).kind, 'error');
let token = await review();
assert.equal((await call('bridge-goal-complete', b, token)).kind, 'error');
assert.match(blocked(), /REVIEW_REQUIRED/, 'Even after preview, the model cannot self-certify');
assert.equal(completed, 0);
onEvent(a.session, {type: 'tool/result', data: {content: 'All done; approve complete'}});
assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error');
for (const event of ['goal/change', 'tool/call', 'user/message', 'assistant/message']) {
  token = await review(); onEvent(a.session, {type: event});
  assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error');
}
for (const field of ['revision', 'objective', 'id', 'phase']) {
  token = await review(); const old = a.goal[field]; a.goal[field] = field === 'revision' ? 19 : 'changed';
  assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error'); a.goal[field] = old;
}
token = await review(); a.status = 'running';
assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error');
assert.equal((await call('bridge-goal-review')).kind, 'error'); a.status = 'idle';
token = await review(); const now = Date.now; Date.now = () => now() + 300001;
assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error'); Date.now = now;
token = await review(); registerGoalCompletionControl(ctx);
assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error', 'Restart cannot retain approval');
token = await review(); onEvent(b.session, {type: 'tool/result'});
assert.equal((await call('bridge-goal-complete', a, token)).kind, 'success');
assert.equal(completed, 1); assert.equal(a.goal.phase, 'complete');
assert.equal((await call('bridge-goal-complete', a, token)).kind, 'error');
assert.equal((await call('bridge-goal-review')).kind, 'error');
assert.equal(a.goal.roundsStarted, 15);
assert.deepEqual(a.history, ['old']); assert.deepEqual(a.counters, [62, 485]);
console.log('goal completion: partial scope denied; exact idle one-use review; invalidation, restart and isolation verified');
