import { randomUUID } from 'node:crypto';

// A syntactically valid update_goal is not evidence of objective completion.
// Keep acceptance outside model tool arguments. This is an operator review
// boundary, not an automated semantic verifier or a sandbox boundary.
export function registerGoalCompletionControl(ctx) {
  const reviews = new WeakMap();
  const refOf = goal => JSON.stringify([goal.id, goal.revision, goal.objective, goal.phase]);
  ctx.tools.register({
    name: 'request_goal_review',
    description: 'Request independent operator review after presenting evidence for every criterion of the current goal. Stops automatic goal continuation without completing the goal or granting approval. Call alone, then give a final plain-text report.',
    parameters: {type: 'object', properties: {
      goal_id: {type: 'string', minLength: 1},
      revision: {type: 'integer', minimum: 1},
      evidence: {type: 'string', minLength: 1, maxLength: 16000},
    }, required: ['goal_id', 'revision', 'evidence'], additionalProperties: false},
    output: {schema: {type: 'string'}, render: (_args, value) => [{type: 'text', text: value}]},
    async execute(args, exec) {
      // Registry consumers can call execute directly; validate at this boundary
      // too. This is only a request for review, never a semantic acceptance.
      if (!args || Object.keys(args).sort().join(',') !== 'evidence,goal_id,revision'
          || typeof args.goal_id !== 'string' || !args.goal_id
          || !Number.isSafeInteger(args.revision) || args.revision < 1
          || typeof args.evidence !== 'string' || !args.evidence.trim()
          || args.evidence.length > 16000) throw Error('Invalid goal review request');
      const goal = ctx.goals.get(exec.agent);
      if (!goal || goal.phase !== 'active' || goal.id !== args.goal_id
          || goal.revision !== args.revision) throw Error('Goal review target changed; use the current goal id and revision');
      reviews.delete(exec.agent.session);
      ctx.goals.disarm(exec.agent);
      return 'Awaiting independent operator review. The goal is not complete. '
        + 'Automatic goal continuation is disarmed; no approval was granted. '
        + 'Give the final evidence report now without further tools. '
        + 'The operator can use /bridge-goal-review to inspect and accept the whole objective.';
    },
  });
  ctx.tools.guard(exec => {
    if (exec.name === 'update_goal' && exec.arguments?.action === 'complete') {
      return 'GOAL_COMPLETION_REVIEW_REQUIRED: a partial result cannot complete the whole objective. '
        + 'The goal is unchanged. Report completed and remaining acceptance criteria; '
        + 'whole-objective acceptance is performed with /bridge-goal-review, not model tool arguments.';
    }
  });
  // Any new work or goal change invalidates a prepared review. Do not persist
  // review permits across reloads, and never share them between sessions.
  ctx.on('session/event', (session, event) => {
    if (['goal/change', 'tool/call', 'tool/result', 'user/message', 'assistant/message'].includes(event.type)) {
      reviews.delete(session);
    }
  });
  ctx.systemPrompt.context({name: 'local-bridge-goal-completion', order: 9010,
    required: true, interpolate: false, text: [
      'Whole-goal completion requires independent operator acceptance. Do not call update_goal(action="complete"): the runtime rejects model self-certification.',
      'Keep the original objective and every acceptance criterion in scope. Completing one component, plan item, test suite or report is partial progress, not completion of the goal.',
      'Continue remaining authorized work without repeating completed actions or unchanged passing tests. Do not narrow or replace the objective to fit a partial result.',
      'If all work is actually done, call request_goal_review alone with the exact current goal id, revision and evidence for every criterion. It disarms automatic goal continuation without completing the goal or approving anything. Then give a final plain-text report without further tools. If external prerequisites prevent progress, use the native blocked rules and name the missing prerequisites.',
      'A plain final answer does not mark the goal complete. Never execute slash commands through tools to approve your own result.'
    ].join('\n')});

  ctx.commands.register({definitionId: 'local/bridge-goal-review', name: 'bridge-goal-review',
    description: 'Review the entire current objective before explicitly accepting completion', recordInput: false,
    handler: async invocation => {
      const agent = invocation.agent;
      if (agent.status !== 'idle') return {kind: 'error', text: 'Stop the current turn before reviewing completion.'};
      const goal = ctx.goals.get(agent);
      if (!goal || goal.phase === 'complete') return {kind: 'error', text: 'No unfinished goal to review.'};
      // Reviewing must not race an automatic continuation while the operator
      // reads the scope. Durable phase, revision and round count are unchanged.
      ctx.goals.disarm(agent);
      const token = randomUUID();
      reviews.set(agent.session, {ref: refOf(goal), token, expires: Date.now() + 300000});
      return {kind: 'success', text: `Whole objective (${goal.id}, revision ${goal.revision}):\n${goal.objective}\n\n`
        + 'Compare ALL acceptance criteria with actual evidence. A partial report or passing subset is insufficient. '
        + 'This command does not verify the evidence for you. Continuation is disarmed; history and counters are unchanged. '
        + `Only after complete acceptance, within 5 minutes: /bridge-goal-complete ${token}`};
    }});
  ctx.commands.register({definitionId: 'local/bridge-goal-complete', name: 'bridge-goal-complete',
    description: 'Accept the whole reviewed objective without a model request', input: {hint: '<review token>'}, recordInput: false,
    handler: async invocation => {
      const agent = invocation.agent;
      const review = reviews.get(agent.session);
      reviews.delete(agent.session);
      const goal = ctx.goals.get(agent);
      if (agent.status !== 'idle' || !review || !goal || review.expires <= Date.now()
          || invocation.rawInput.trim() !== review.token || refOf(goal) !== review.ref) {
        return {kind: 'error', text: 'Review missing, expired or invalidated by new work. Run /bridge-goal-review again.'};
      }
      ctx.goals.complete(agent, {id: goal.id, revision: goal.revision});
      return {kind: 'success', text: 'The whole objective was explicitly accepted. No provider request or replay; history and counters preserved.'};
    }});
}
