"""Opt-in bounded private capture of failed replies, without another request."""

import json
import hashlib
import os
import time
from pathlib import Path

from deepseek.auth import write_private_json

FAILURE_FILE = Path(__file__).resolve().parent.parent / "session" / "last-toolcall-error.json"
MAX_BYTES = 4 * 1024 * 1024
MAX_ARCHIVED = 32


def save_tool_failure(reply, request):
    if os.getenv("TOOLCALL_DIAGNOSTICS", "").lower() not in ("1", "true", "yes", "on"):
        return
    # No prompt/history, account credentials or headers. The private response
    # identity and an irreversible request digest permit later binding review;
    # they never authorize delivery or a new generation on their own.
    # The reply itself may contain private source, so never send it to stdout.
    data = {"captured_at": time.time(), "model": request.model, "finish_reason": reply.finish_reason,
            "reply": reply.text, "tools": request.tools, "tool_choice": request.tool_choice}
    try:
        if request._dispatch_contract is not None:
            data['dispatch_contract'] = request._dispatch_contract
        from deepseek.client import _decode_cid
        from .config import model_provider
        from .conversation_cache import digest
        if model_provider(request.model) == 'deepseek':
            try:
                sid, parent = _decode_cid(reply.conversation_id)
            except ValueError:
                sid, parent = None, None
            if sid is not None and parent is not None:
                data['recovery_binding'] = {
                    'conversation_id': reply.conversation_id,
                    'request_digest': digest(request, request.messages),
                    'delivery_authorized': False,
                }
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
            print("[toolcalls] private diagnostic exceeds size limit; not saved", flush=True)
            return
        from deepseek.stream_diagnostics import StreamCapture
        evidence = getattr(reply, '_deepseek_stream_evidence', None)
        if (isinstance(evidence, tuple) and len(evidence) == 3
                and isinstance(evidence[0], StreamCapture)
                and 'recovery_binding' in data):
            capture, session_id, parent_id = evidence
            saved = capture.save(reason='invalid_tool_reply', session_id=session_id,
                                 parent_id=parent_id,
                                 directory=FAILURE_FILE.parent / 'failed-tool-streams')
            data['stream_capture_status'] = capture.save_status
            if saved is not None:
                data['stream_capture'] = saved.name
        if len(json.dumps(data, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            print('[toolcalls] private diagnostic exceeds size limit; not saved', flush=True)
            return
        write_private_json(FAILURE_FILE, data)
        # Keep distinct failures for offline regression, not only the last one.
        # Bound disk usage; never evict old evidence or collect prompt/account data.
        fingerprint = {k: v for k, v in data.items() if k != 'captured_at'}
        failure_digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True,
                                                   ensure_ascii=False).encode()).hexdigest()
        archive = FAILURE_FILE.parent / 'toolcall-failures'
        target = archive / (failure_digest + '.json')
        if not target.exists() and (not archive.exists() or
                                  len(list(archive.iterdir())) < MAX_ARCHIVED):
            write_private_json(target, data)
    except (OSError, TypeError, ValueError):
        # Diagnostics must neither mask the original failure nor leak raw data.
        print("[toolcalls] could not save private diagnostic", flush=True)
