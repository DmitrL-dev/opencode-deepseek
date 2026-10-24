// Runs an owned native goal and its operator command lifecycle; no accounts.
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
export const name = 'owned-review-cycle';
export const inject = ['agents', 'goals', 'sessions', 'commands', 'agentDefaultModel'];
export async function apply(ctx, config) {
  const {installModelSelection} = await import(pathToFileURL(join(config.libraryRoot, 'dsh-agent/lib/index.js')).href);
  const exit = ctx.get('appExit');
  let agent, steps = 0, finishing = false;
  const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
  const timer = setTimeout(() => { agent?.cancel(); exit(1); }, 20000);
  const finish = async (code, reason) => {
    if (finishing) return;
    finishing = true; clearTimeout(timer);
    await agent.whenIdle(); await ctx.sessions.flush(agent.session);
    emit({type:'fixture/end', reason, steps, sessionId:agent.session.id, goal:ctx.goals.get(agent)});
    exit(code);
  };
  ctx.on('agent/pre-step', ({agent: subject}, next) => {
    if (subject !== agent) return next();
    assert.ok(++steps <= 7, 'No extra request after review readiness');
    return next();
  });
  ctx.on('session/event', (session,event) => {
    if (session !== agent?.session) return;
    if (['tool/call','tool/result','goal/change','turn/end'].includes(event.type)) emit({type:'fixture/event',event});
    if (event.type !== 'turn/end') return;
    const goal=ctx.goals.get(agent);
    if (event.data.reason.kind !== 'completed') {void finish(1,event.data.reason);return;}
    if (goal.activation === 'disarmed') void finish(0,'awaiting_review');
  });
  const start = async () => { try {
    await ctx.get('loader')?.await();
    const selection=ctx.agentDefaultModel.currentSelection();
    const options={agentOptions:{provider:selection.provider,model:selection.model},
      setup:scope=>{ installModelSelection(scope,{current:selection,assembled:undefined}); }};
    ({agent}=config.sessionId
      ? await ctx.agents.resume({...options,resumeSessionId:config.sessionId})
      : await ctx.agents.create({...options,sessionId:`session-${randomUUID()}`,meta:{cwd:process.cwd()}}));
    assert.equal(agent.session.header.cwd,process.cwd());
    if (!config.sessionId) {
      ctx.goals.create(agent,{objective:'Read the fixture, write report.md, read it back, then request independent review with evidence.',maxGoalRounds:4});
    } else if (config.inspectComplete) {
      assert.equal(ctx.goals.get(agent).phase,'complete');
      await finish(0,'completion_persisted');
    } else {
      const before=ctx.goals.get(agent);
      assert.equal(before.phase,'active'); assert.equal(before.activation,'disarmed');
      const command=async line=>(await ctx.commands.execute(agent,line,[],new AbortController().signal)).result;
      assert.equal((await command('/bridge-goal-complete expired-prior-process-token')).kind,'error');
      const preview=await command('/bridge-goal-review');
      assert.equal(preview.kind,'success'); assert.ok(preview.text.includes(before.objective));
      assert.equal(ctx.goals.get(agent).phase,'active');
      const token=preview.text.split('/bridge-goal-complete ')[1];
      assert.equal((await command('/bridge-goal-complete '+token)).kind,'success');
      assert.equal(ctx.goals.get(agent).phase,'complete');
      assert.equal(ctx.goals.get(agent).roundsStarted,before.roundsStarted);
      assert.equal((await command('/bridge-goal-complete '+token)).kind,'error');
      await finish(0,'operator_accepted');
    }
  } catch(error) {
    clearTimeout(timer); emit({type:'fixture/setup-error',message:error.message,stack:error.stack}); exit(1);
  }};
  void start();
}
