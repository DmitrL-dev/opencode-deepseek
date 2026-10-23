// Drives only an owned test Session through the installed native scheduler.
import {randomUUID} from 'node:crypto';
import {isAbsolute, join} from 'node:path';
import {pathToFileURL} from 'node:url';
export const name = 'owned-native-goal-fixture';
export const inject = ['agents', 'goals', 'sessions', 'agentDefaultModel'];

export async function apply(ctx, config) {
  if (!isAbsolute(config.libraryRoot) || !Number.isSafeInteger(config.maxSteps)
      || config.maxSteps < 1 || config.maxSteps > 12) throw Error('Invalid fixture bounds');
  const {installModelSelection} = await import(pathToFileURL(join(config.libraryRoot, 'dsh-agent/lib/index.js')).href);
  const exit = ctx.get('appExit');
  let agent, finishing = false, steps = 0, timer;
  const emit = data => process.stdout.write(JSON.stringify(data) + '\n');
  ctx.on('tools/result', (exec, result) => {
    if (exec.agent === agent && result.isError) emit({type: 'fixture/tool-error',
      tool: exec.name, error: result.error?.info});
  });
  const finish = async (code, reason) => {
    if (finishing) return;
    finishing = true;
    clearTimeout(timer);
    await agent?.whenIdle();
    if (agent) await ctx.sessions.flush(agent.session);
    emit({type: 'fixture/end', code, reason, steps, sessionId: agent?.session.id,
      goal: agent ? ctx.goals.get(agent) : null});
    exit(code);
  };
  ctx.on('agent/pre-step', ({agent: subject}, next) => {
    if (subject !== agent) return next();
    if (finishing || steps >= config.maxSteps) {
      ctx.goals.disarm(agent);
      throw Error('Owned fixture request bound reached');
    }
    steps++;
    return next();
  });
  ctx.on('session/event', (session, event) => {
    if (!agent || session !== agent.session) return;
    if (['goal/change', 'user/message', 'assistant/message', 'tool/call', 'tool/result', 'turn/end'].includes(event.type)) {
      emit({type: 'fixture/event', event});
    }
    if (event.type !== 'turn/end') return;
    // Observe the shipped scheduler's disarming; do not do it on its behalf.
    // Capture this turn's state: a delayed observer must not finish a newer
    // round merely because that round has already completed the goal.
    const {phase, activation} = ctx.goals.get(agent) || {};
    setTimeout(() => {
      if (finishing) return;
      if (event.data.reason.kind !== 'completed') void finish(1, event.data.reason);
      else if (phase === 'complete') void finish(0, 'complete');
      else if (activation === 'disarmed') void finish(2, 'disarmed');
    }, 50);
  });
  const start = async () => {
    try {
      await ctx.get('loader')?.await();
      const selection = ctx.agentDefaultModel.currentSelection();
      const options = {agentOptions: {provider: selection.provider, model: selection.model},
        setup: scope => { installModelSelection(scope, {current: selection, assembled: undefined}); }};
      const sessionId = config.sessionId || `session-${randomUUID()}`;
      ({agent} = config.sessionId
        ? await ctx.agents.resume({...options, resumeSessionId: sessionId})
        : await ctx.agents.create({...options, sessionId, meta: {cwd: process.cwd()}}));
      if (agent.session.header.cwd !== process.cwd()) throw Error('Fixture workspace mismatch');
      timer = setTimeout(() => {
        ctx.goals.disarm(agent);
        agent.cancel();
        void finish(1, 'deadline');
      }, 15000);
      if (config.sessionId) {
        const goal = ctx.goals.get(agent);
        if (goal?.phase !== 'active' || goal.activation !== 'disarmed') throw Error('Not resumable');
        ctx.goals.resume(agent, {id: goal.id, revision: goal.revision});
      } else {
        ctx.goals.create(agent, {objective: 'Complete the owned two-stage fixture and verify its result.', maxGoalRounds: 3});
      }
    } catch (error) {
      emit({type: 'fixture/setup-error', message: error.message, stack: error.stack});
      clearTimeout(timer);
      exit(1);
    }
  };
  void start();
}
