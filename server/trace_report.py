"""Count live tool-step outcomes from private TOOLCALL_LOG traces, offline.

Usage: python -m server.trace_report logs
Reads only local trace files and makes no account or network requests. Output
contains counts and fixed bridge error categories, never prompts, replies,
tool arguments or conversation IDs.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

from .toolcall_logging import SEPARATOR

_NAMES = {line: name for name, line in SEPARATOR.items()}
_DATA = re.compile(r'(?m)^[ \t]*data:[ \t]*')

# Bridge error templates, by who produced the failure. A model step is one the
# provider generated; bridge-state and request refusals say nothing about it.
_CATEGORIES = (
    ('model_format', (
        'Invalid or incomplete DSML', 'Unsupported tool-call markup',
        'The tool_calls block contains invalid JSON', 'Expected one complete fenced',
        'Each tool call must be an object', 'Tool arguments contain invalid JSON',
        'Tool arguments must be a JSON object', 'Tool arguments contain non-finite',
        'Tool call contains ambiguous envelope', 'Provider returned no answer text')),
    ('model_schema', (
        'Tool arguments do not satisfy', 'The model called an unadvertised tool',
        'The model called a tool', 'The model did not emit the required',
        'The model started a tool call while')),
    ('model_incomplete', (
        'The provider reached its output limit', 'The provider did not finish',
        'Could not finish the truncated', 'The format correction did not finish')),
)
_REPAIR_FAILED = 'The single format correction also failed; no further correction was requested. '
MODEL_OUTCOMES = ('calls', 'text', 'model_format', 'model_schema', 'model_incomplete')


def _blocks(text):
    name, body = None, []
    for line in text.splitlines():
        if line in _NAMES:
            if name:
                yield name, '\n'.join(body).strip()
            name, body = _NAMES[line], []
        elif name:
            body.append(line)
    if name:
        yield name, '\n'.join(body).strip()


def _responses(text):
    """Join consecutive to_agent blocks: each SSE frame is recorded separately."""
    frames = []
    for name, body in _blocks(text):
        if name == 'to_agent':
            frames.append(body)
        elif frames:
            yield '\n'.join(frames)
            frames = []
    if frames:
        yield '\n'.join(frames)


def _objects(body):
    if body.startswith('{'):
        try:
            return [json.loads(body)]
        except ValueError:
            return []
    decoder, objects = json.JSONDecoder(), []
    for match in _DATA.finditer(body):
        try:
            objects.append(decoder.raw_decode(body, match.end())[0])
        except ValueError:
            continue
    return objects


def classify(body):
    finish, error = None, None
    for item in _objects(body):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('error'), dict):
            error = item['error']
        for choice in item.get('choices') or ():
            finish = choice.get('finish_reason') or finish
    if error is None:
        return {'tool_calls': 'calls', 'stop': 'text'}.get(finish, 'unknown')
    if error.get('type') != 'invalid_tool_response':
        return 'request_refused'
    message = str(error.get('message') or '')
    if message.startswith(_REPAIR_FAILED):
        # The bridge prefixes the corrected reply's own error: classify that.
        message = message[len(_REPAIR_FAILED):]
        for category, prefixes in _CATEGORIES:
            if message.startswith(prefixes):
                return category
        return 'model_format'
    for category, prefixes in _CATEGORIES:
        if message.startswith(prefixes):
            return category
    return 'bridge_state'


def report(log_dir):
    days, total = {}, Counter()
    for path in sorted(Path(log_dir).glob('*.log')):
        day = path.name[:4] if path.name[:4].isdigit() else 'unknown'
        for body in _responses(path.read_text(encoding='utf-8', errors='replace')):
            outcome = classify(body)
            total[outcome] += 1
            days.setdefault(day, Counter())[outcome] += 1

    def summary(counts):
        model = sum(counts[key] for key in MODEL_OUTCOMES)
        ok = counts['calls'] + counts['text']
        return {'outcomes': dict(sorted(counts.items())), 'model_steps': model,
                'model_step_success': round(ok / model, 3) if model else None}

    return {**summary(total), 'by_day': {day: summary(counts) for day, counts in sorted(days.items())}}


if __name__ == '__main__':
    print(json.dumps(report(sys.argv[1] if len(sys.argv) > 1 else 'logs'), indent=2))
