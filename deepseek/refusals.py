"""Context-aware refusals and bounded, private metadata; never store response text."""
import json
import logging
import os
from pathlib import Path
import stat
import time
import uuid

import httpx
from providers.access import ProviderRejected, rejection, current_attempt
from settings import ROOT

DIRECTORY = ROOT / 'session' / 'refusal-diagnostics'
MAX_FILES = 64
PHASES = frozenset(('create_chat', 'challenge', 'completion', 'parse_stream', 'before_request'))
GLOBAL_CODES = {40002: 'session_expired', 40003: 'session_expired',
                40012: 'account_banned', 40029: 'ip_restricted',
                50006: 'account_muted', 40300: 'pow_rejected', 40301: 'pow_rejected'}
GATEWAYS = frozenset(('aws_captcha', 'aws_challenge', 'cloudflare_challenge'))


def number(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def classify(data, *, completion=False, business=False):
    value = data if isinstance(data, dict) else {}
    raw = number(value.get('code'))
    envelope = value.get('data') if isinstance(value.get('data'), dict) else {}
    biz = number(value.get('biz_code') if business else envelope.get('biz_code'))
    if business:
        code = ('account_muted' if completion else 'account_restricted') if biz == 5 else None
    elif raw in GLOBAL_CODES:
        code = GLOBAL_CODES[raw]
    elif completion and raw == 5:
        code = 'account_muted'
    else:
        code = None
    error = ProviderRejected(code) if code else rejection(data)
    error.raw_code, error.business_code = raw, biz
    return error


def check_response(response, phase):
    status = response.status_code
    headers = response.headers
    gateway = None
    if status == 405 and headers.get('x-amzn-waf-action') == 'captcha':
        gateway = 'aws_captcha'
    elif status == 202 and headers.get('x-amzn-waf-action') == 'challenge':
        gateway = 'aws_challenge'
    elif status in (403, 429) and headers.get('cf-mitigated') == 'challenge':
        gateway = 'cloudflare_challenge'
    if gateway:
        error = ProviderRejected('gateway_challenge')
        error.gateway = gateway
    elif not 200 <= status < 300:
        data = None
        try:
            if len(response.content) <= 65536:
                data = response.json()
        except (ValueError, httpx.ResponseNotRead):
            pass
        # Do not infer an account ban from HTTP status or a challenge alone.
        error = classify(data, completion=phase == 'completion') if isinstance(data, dict) else rejection(status=status)
        if error.code == 'upstream_failure':
            fallback = rejection(status=status)
            fallback.raw_code = getattr(error, 'raw_code', None)
            fallback.business_code = getattr(error, 'business_code', None)
            error = fallback
    else:
        return
    error.http_status, error.phase = status, phase
    save(error, phase=phase, status=status)
    raise error


def save(error, *, phase, status=None):
    """Best effort metadata only; failures never release a guard or replay a request."""
    if not isinstance(error, ProviderRejected) or getattr(error, '_refusal_saved', False):
        return
    code = error.code
    from providers.access import MESSAGES
    attempt = current_attempt('deepseek')
    identifier = getattr(attempt, 'id', None)
    identifier = identifier if isinstance(identifier, str) and len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier) else None
    record = {'attempt_id': identifier, 'version': 1, 'at': time.time(), 'reason': code if code in MESSAGES else 'upstream_failure',
              'phase': phase if phase in PHASES else 'before_request',
              'http_status': number(status), 'api_code': number(getattr(error, 'raw_code', None)),
              'biz_code': number(getattr(error, 'business_code', None)),
              'gateway': getattr(error, 'gateway', None) if getattr(error, 'gateway', None) in GATEWAYS else None}
    try:
        DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = DIRECTORY.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise OSError('Private directory required')
        if len(list(DIRECTORY.iterdir())) >= MAX_FILES:
            raise OSError('Diagnostic directory full')
        path = DIRECTORY / (uuid.uuid4().hex + '.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(record, output, separators=(',', ':'))
        error._refusal_saved = True
    except OSError:
        logging.getLogger(__name__).warning('Private refusal metadata not saved; safety pause remains required')
