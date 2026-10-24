import { randomUUID } from 'node:crypto';

// Runtime context is appended by Harness as a native user/message event. Do
// not change the system prompt or rewrite historical read/tool results: both
// are part of the bridge's exact, one-use conversation binding.
export function registerReadContext(ctx) {
  const epoch = randomUUID();
  ctx.systemPrompt.context({
    name: 'local-bridge-file-observation-runtime',
    order: 9000,
    required: true,
    interpolate: false,
    text: [
      `Filesystem observation runtime: ${epoch}.`,
      'Native file observations are in memory and do not survive a Harness restart or plugin reload.',
      'When this runtime identifier changes, earlier read results remain historical context, but do not authorize edits in this runtime.',
      'Before editing or replacing an existing file, call the native read tool for that exact file_path in this runtime and wait for its successful result. Then issue edit/write in a later response.',
      'A bash/cat result, an old read in the transcript, or a read and edit in the same response does not satisfy this requirement.',
      'Once read has succeeded in this runtime, do not repeat it without a reason. Native version checks still decide whether the file changed.',
      'Preserve completed work, test results and the goal. Repeat only the necessary read, not successful edits, commands or tests.',
      'This notice neither executes tools nor retries a failed operation, and does not grant filesystem access.'
    ].join('\n')
  });
  return epoch;
}
