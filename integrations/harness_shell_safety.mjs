import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'bridge-shell-safety';
export const contractVersion = 'harness-contract-v3';
export const inject = ['shell', 'tools', 'commands', 'goals'];

// This is execution policy, not a rewrite of the model's shell source. Bash
// retains its normal explicit if/! / && / || handling of expected failures.
export function hardenSpec(spec, cacheRoot) {
  // Explicit opt-in for searches where zero matches is an expected result.
  // Never normalize a whole pipeline's status: its producer may also exit 1.
  const optionalGrep = 'bridge_grep_optional() {\n'
    + '  local status=0\n'
    + '  command grep "$@" || status=$?\n'
    + '  if [ "$status" -eq 1 ]; then return 0; fi\n'
    + '  return "$status"\n'
    + '}\n';
  const helper = fileURLToPath(new URL('./harness_gh_checks.py', import.meta.url));
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  const ghChecks = 'gh() {\n'
    + '  if [ "${1-}" = pr ] && [ "${2-}" = checks ]; then\n'
    + '    local gh_binary; gh_binary=$(type -P gh) || return $?\n'
    + '    shift 2\n'
    + '    command python3 ' + quote(helper) + ' "$gh_binary" "$@"\n'
    + '  else command gh "$@"; fi\n'
    + '}\n';
  return {...spec, command: 'set -e -o pipefail\n' + optionalGrep + ghChecks + spec.command,
    env: {...spec.env, XDG_CACHE_HOME: cacheRoot}};
}

export function failedResult(exec, result) {
  if (result.isError) return true;
  if (exec.name !== 'bash' || result.value?.kind !== 'foreground') return false;
  const value = result.value;
  return value.exitCode !== 0 || value.signal != null || value.timedOut
    || value.aborted || value.sandbox?.denied === true;
}

export function apply(ctx) {
  // Random, owner-only cache; no HOME/GH_CONFIG_DIR changes or credential copy.
  // Keep it until OS temp cleanup: background readers must not lose their files.
  const cacheRoot = mkdtempSync(join(process.platform === 'linux' ? '/tmp' : tmpdir(), 'harness-gh-'));
  const shell = ctx.shell;
  ctx.effect(() => {
    const original = shell.resolve;
    function resolve(request) { return hardenSpec(original.call(this, request), cacheRoot); }
    shell.resolve = resolve;
    return () => { if (shell.resolve === resolve) shell.resolve = original; };
  });
  ctx.on('tools/result', (exec, result) => {
    if (failedResult(exec, result) && exec.agent?.status === 'running') {
      // Cancellation alone does not revoke an armed goal's continuation
      // authority. Disarm first, without changing its durable phase/revision.
      // Still cancel if disarm throws: result observers cannot propagate errors
      // into the scheduler, and an idle transition must not start another step.
      try { ctx.goals.disarm(exec.agent); }
      finally { exec.agent.cancel({kind: 'user'}, {keepInbox: true}); }
      // A native stop preserves completed tool results and does not ask the
      // provider to diagnose/retry. Keep queued user input, like the Stop button.
    }
  });
  ctx.commands.register({definitionId: 'local/bridge-shell-status', name: 'bridge-shell-status',
    description: 'Local shell pipeline protection and gh cache status', recordInput: false,
    handler: async () => {
      const spec = ctx.shell.resolve({command: ':'});
      const active = spec.command.startsWith('set -e -o pipefail\n') && spec.env.XDG_CACHE_HOME === cacheRoot
        && typeof ctx.goals.disarm === 'function';
      return {kind: active ? 'success' : 'error', text: active
        ? `Shell safety active (${contractVersion}): errexit + pipefail; goal disarm before native stop on tool failure; validated gh pr checks status; private temporary gh cache. No command or provider request was sent.`
        : 'Shell safety is not active in the current executor. Do not resume until the profile is reloaded.'};
    }});
}
