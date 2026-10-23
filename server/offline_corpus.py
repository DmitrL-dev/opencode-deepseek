"""Replay private diagnostics against reviewed expectations without account I/O.

Usage: python -m server.offline_corpus session/protocol-corpus.json
The manifest pins source hashes and expected calls; it is private, not a fixture
to publish. Output contains counts only, never source, commands or arguments.
Each case selects protocol=json|dsml and implementation=legacy|new explicitly;
omitted fields retain the original json/legacy policy, not the local .env.
"""
import hashlib
import json
from pathlib import Path
import sys

from .openai_format import ToolCallError
from .tool_protocol import validate_reply
from .tool_protocol_new import validate_reply as validate_new_reply


def _outcome(data, item):
    if (not isinstance(data, dict) or not isinstance(data.get('reply'), str)
            or not isinstance(data.get('tools'), (list, type(None)))
            or not isinstance(data.get('finish_reason'), str)):
        raise ValueError('Invalid corpus diagnostic')
    protocol = item.get('protocol', 'json')
    implementation = item.get('implementation', 'legacy')
    if (protocol not in ('json', 'dsml') or implementation not in ('legacy', 'new')
            or not isinstance(item['provider'], str) or not item['provider']):
        raise ValueError('Invalid corpus validation policy')
    validate = validate_reply if implementation == 'legacy' else validate_new_reply
    native = protocol == 'dsml'
    try:
        content, calls = validate(
            data['reply'], data['tools'], data.get('tool_choice'),
            finish_reason=data['finish_reason'], provider=item['provider'],
            allow_dsml_preamble=native, allow_dsml_wrapper_tolerances=native,
        )
    except ToolCallError:
        return {'kind': 'reject'}
    return {'kind': 'calls', 'calls': [
        {'name': c['function']['name'],
         'arguments': json.loads(c['function']['arguments'])} for c in calls
    ]} if calls else {'kind': 'text', 'text': content}


def replay(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    failures = []
    matched = {'calls': 0, 'text': 0, 'reject': 0}
    for index, item in enumerate(manifest):
        try:
            raw = (manifest_path.parent / item['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Corpus source changed')
            actual = _outcome(json.loads(raw), item)
            if actual != item['expected']:
                raise ValueError('Corpus outcome changed')
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Broken evidence/settings are failed checks, never successful
            # expected refusals. Do not print private payloads or paths.
            failures.append(index)
        else:
            matched[actual['kind']] += 1
    return {'cases': len(manifest), 'failed_indices': failures,
            'matched_outcomes': matched}


if __name__ == "__main__":
    report = replay(sys.argv[1])
    print(json.dumps(report))
    sys.exit(bool(report["failed_indices"]))
