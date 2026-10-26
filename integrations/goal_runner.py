"""Run one Harness task headless through the bridge, with bounded resumes.

Usage: bin/ds-goal [--dir PATH] [--max-resumes N] "task text"
       bin/ds-goal --continue session/goal-runs/RUN ["message"]

Each run gets an isolated Harness home under session/goal-runs/ with the
bridge route, shell safety and disabled subagents, so the desktop profile is
never edited. Only a stop caused by the model's own tool-call reply (format,
schema or an unfinished reply) is resumed, at most --max-resumes times, by a
new "continue" message in the same session; a stored reply is never replayed.
Shell failures, budgets, pauses and history refusals stop the run for review.
Goal acceptance remains manual; --continue resumes a stopped run after review.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DSH = '/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh'
BRIDGE_LOG = ROOT / 'session' / 'harness-bridge.log'
RESUMABLE = frozenset({'model_format', 'model_schema', 'model_incomplete'})
TEST_RULE = ('Run tests only with `bridge_unittest python3 discover -s tests` or '
             '`bridge_unittest python3 -v tests.test_module`: the arguments after the interpreter '
             'go to unittest, never add `-m unittest`. Failing assertions are reported for repair; '
             'a test error, an import error or any other failing shell command stops the task.')
RESUME = ('Continue the same task from where it stopped. Your previous reply was not '
          'executed because its tool call was invalid; nothing from it ran. ' + TEST_RULE)


def build_overlay(run_home, base_url, model):
    """Bridge route + shell safety + no subagents; contains the bridge token."""
    from . import harness, harness_shell_safety as shell
    models = harness.discover(base_url)
    document = harness.patch_document([], base_url, models, model)
    entry = harness.install_plugin_copy(run_home, shell.IDENTITY, shell.SOURCES)
    document = shell.patch_document(document, entry, allow_read_repair=False)
    for key in ('tool-subagent', 'tool-subagent-fork', 'tool-subagent-control',
                'tool-subagent-list-agents'):
        document.append({'id': key, 'disabled': True})
    return harness.render(document)


def read_events(path):
    """Summarize one headless --json event stream."""
    summary = {'session': None, 'steps': 0, 'tools': Counter(), 'end': None, 'final': ''}
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get('type') if isinstance(event, dict) else None
        if kind == 'session':
            summary['session'] = event.get('sessionId')
        elif kind == 'tool_result':
            summary['tools'][str(event.get('status'))] += 1
        elif kind == 'status' and event.get('phase') == 'step_end':
            summary['steps'] += 1
        elif kind == 'status' and event.get('phase') == 'turn_end':
            summary['end'] = event.get('reason') or {}
        elif kind == 'final':
            summary['final'] = event.get('text') or ''
    return summary


def stop_category(end):
    """completed, a model_* category, aborted, or bridge_state/unknown."""
    from server.trace_report import error_category
    kind = (end or {}).get('kind')
    if kind == 'completed':
        return 'completed'
    if kind == 'aborted':
        return 'aborted'
    if kind == 'error':
        return error_category(str(((end or {}).get('error') or {}).get('message') or ''))
    return 'unknown'


def _log_offset(bridge_log):
    try:
        return Path(bridge_log).stat().st_size
    except OSError:
        return None


def _corrections(bridge_log, offset):
    if offset is None:
        return {}
    try:
        with Path(bridge_log).open('rb') as stream:
            stream.seek(offset)
            text = stream.read().decode('utf-8', errors='replace')
    except OSError:
        return {}
    counts = Counter(line.split('format_correction=', 1)[1].strip()
                     for line in text.splitlines() if 'format_correction=' in line)
    return dict(sorted(counts.items()))


def run(task, workdir, *, dsh, max_resumes, base_url, model, runs_root=None, attempt_timeout=None,
        run_dir=None, session=None, logs_dir=ROOT / 'logs', bridge_log=BRIDGE_LOG):
    """New run in runs_root, or with run_dir + session continue that run."""
    started = time.time()
    if run_dir is None:
        Path(runs_root).mkdir(parents=True, mode=0o700, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'), dir=runs_root))
        (run_dir / 'dsh').mkdir(mode=0o700)
    # dsh runs in the project directory: every path handed to it must be absolute.
    run_dir = Path(run_dir).resolve()
    home = run_dir / 'dsh'
    prompt = task.rstrip() + '\n\n' + TEST_RULE
    previous = len(list(run_dir.glob('events-*.jsonl')))
    data = build_overlay(home, base_url, model)
    overlay = run_dir / 'bridge.patch.yml'
    overlay.unlink(missing_ok=True)  # left by an interrupted run
    descriptor = os.open(overlay, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as out:
        out.write(data)
    env = {**os.environ, 'DSH_HOME': str(home), 'DSH_TELEMETRY_MODE': 'DISABLED',
           'NO_PROXY': '127.0.0.1,::1'}
    offset = _log_offset(bridge_log)
    attempts = []
    try:
        while True:
            events = run_dir / f'events-{previous + len(attempts) + 1}.jsonl'
            command = [dsh, '--profile', 'headless', '--patch', str(overlay), '--json']
            if session:
                command += ['--session-id', session]
            with events.open('wb') as out, (run_dir / 'stderr.log').open('ab') as err:
                try:
                    code = subprocess.run(command + [prompt], cwd=workdir, env=env, stdout=out,
                                          stderr=err, timeout=attempt_timeout).returncode
                except subprocess.TimeoutExpired:
                    code = None
            summary = read_events(events)
            session = summary['session'] or session
            category = 'timeout' if code is None else stop_category(summary['end'])
            attempts.append({'exit': code, 'stop': category, 'steps': summary['steps'],
                             'tools': dict(summary['tools']), 'final': summary['final']})
            if category in RESUMABLE and len(attempts) <= max_resumes and session:
                prompt = RESUME
                continue
            break
    finally:
        overlay.unlink(missing_ok=True)
    from server.trace_report import report
    traces = report(logs_dir, since=started)
    result = {'status': attempts[-1]['stop'], 'session': session, 'run_dir': str(run_dir),
              'workdir': str(workdir), 'model': model,
              'resumes': len(attempts) - 1, 'attempts': attempts,
              'format_corrections': _corrections(bridge_log, offset),
              'model_steps': traces['model_steps'], 'model_step_success': traces['model_step_success'],
              'seconds': round(time.time() - started)}
    (run_dir / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('task', nargs='?', help='task text; "-" reads stdin')
    parser.add_argument('--continue', dest='resume', type=Path, metavar='RUN_DIR',
                        help='continue a stopped run from session/goal-runs/')
    parser.add_argument('--dir', type=Path, default=Path.cwd(), help='project directory (default: current)')
    parser.add_argument('--max-resumes', type=int, default=2,
                        help='automatic continues after a model tool-call stop (default 2)')
    parser.add_argument('--model', default='deepseek-expert')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/v1')
    parser.add_argument('--dsh', default=os.getenv('DSH_BIN', DEFAULT_DSH))
    parser.add_argument('--timeout', type=int, default=None, help='seconds per attempt (default: none)')
    args = parser.parse_args(argv)
    if args.max_resumes < 0:
        parser.error('--max-resumes must be non-negative')
    task = sys.stdin.read() if args.task == '-' else (args.task or '')
    options = dict(dsh=args.dsh, max_resumes=args.max_resumes, base_url=args.base_url,
                   attempt_timeout=args.timeout)
    try:
        if args.resume:
            previous = json.loads((args.resume / 'summary.json').read_text())
            if not previous.get('session'):
                parser.error('that run has no Harness session to continue')
            result = run(task.strip() or 'Continue the task.', Path(previous['workdir']),
                         model=previous.get('model', args.model), run_dir=args.resume,
                         session=previous['session'], **options)
        else:
            if not task.strip():
                parser.error('empty task')
            if not args.dir.is_dir():
                parser.error('--dir is not a directory')
            result = run(task, args.dir.resolve(), model=args.model,
                         runs_root=ROOT / 'session' / 'goal-runs', **options)
    except (OSError, ValueError) as exc:
        parser.exit(2, 'ds-goal: setup failed: ' + str(exc) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'attempts'},
                     ensure_ascii=False, indent=2))
    final = result['attempts'][-1]['final']
    if final:
        print('\n' + final)
    if result['status'] != 'completed':
        print('\nStopped: ' + result['status'] + '. Review the project, then continue with: '
              'bin/ds-goal --continue ' + result['run_dir'] + ' "message"', file=sys.stderr)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
