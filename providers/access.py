"""Durable provider attempts and pauses; no upstream response data is stored."""
import argparse
import contextlib
from contextvars import ContextVar
import json
import hashlib
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

import httpx
from settings import ROOT

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

MAX_BUDGET = 2000

PROVIDERS = frozenset(('deepseek', 'qwen', 'grok', 'mistral', 'kimi', 'glm', 'gemini'))
MESSAGES = {
    'local_budget_exceeded': 'Local request budget exhausted. No automatic retry; limits survive restart and resume.',
    'account_restricted': 'The provider restricted this account. Stop and inspect the normal website.',
    'quota_exceeded': 'The provider rejected the request because of a quota or usage limit.',
    'capacity_unavailable': 'The provider has no capacity for this free account right now. Automatic retries are disabled.',
    'access_denied': 'The provider denied access. Inspect sign-in, security and regional restrictions manually.',
    'session_expired': 'The provider rejected the session. Sign in manually before resuming.',
    'upstream_failure': 'The provider request failed or its outcome is uncertain. Automatic replay is disabled.',
    'safety_state_unavailable': 'Cannot persist or read provider safety state. Requests are stopped until it is repaired.',
}
_active = ContextVar('provider_attempt', default=None)


class ProviderRejected(RuntimeError):
    def __init__(self, code='upstream_failure'):
        self.code = code if code in MESSAGES else 'upstream_failure'
        super().__init__(MESSAGES[self.code])


def rejection(detail=None, status=None):
    text = str(detail).lower() if detail is not None else ''
    if 'muted' in text or any(word in text for word in ('banned', 'suspended', 'account blocked')):
        return ProviderRejected('account_restricted')
    if status == 429 or any(word in text for word in ('quota', 'rate_limit', 'rate limit', 'usage limit')):
        return ProviderRejected('quota_exceeded')
    if status == 403 or any(word in text for word in ('forbidden', 'region', 'security', 'access denied')):
        return ProviderRejected('access_denied')
    if status == 401 or any(word in text for word in ('invalid_token', 'token expired', 'unauthorized')):
        return ProviderRejected('session_expired')
    return ProviderRejected()


def current_attempt(provider):
    attempt = _active.get()
    return attempt if attempt is not None and attempt.provider == provider else None


def _file_lock(file, unlock=False):
    if os.name == 'nt':
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX)


def _configured_budgets():
    """Bounded rolling ceilings; MAX_BUDGET is also the persisted ledger size limit."""
    limits = []
    for name, category, defaults in (
        ('HTTP', None, (40, 200)), ('CHATS', 'chat', (3, 6)),
        ('GENERATIONS', 'generation', (12, 60)),
    ):
        values = []
        for suffix, default in zip(('HOUR', 'DAY'), defaults):
            key = f'DEEPSEEK_MAX_{name}_PER_{suffix}'
            raw = os.getenv(key, str(default))
            if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= MAX_BUDGET:
                raise ValueError(f'{key} must be an integer between 1 and {MAX_BUDGET}')
            values.append(int(raw))
        if values[0] > values[1]:
            raise ValueError(f'DEEPSEEK_MAX_{name}_PER_DAY must be >= PER_HOUR')
        limits.extend((window, category, value)
                      for window, value in zip((3600, 86400), values))
    return tuple(limits)


class AccessGuard:
    def __init__(self, path=ROOT / 'session' / 'provider-pauses.json', interval=None):
        self.path = Path(path) if path is not None else None
        self.interval = float(os.getenv('PROVIDER_MIN_INTERVAL', '30') if interval is None else interval)
        if not math.isfinite(self.interval) or not 0 <= self.interval <= 3600:
            raise ValueError('PROVIDER_MIN_INTERVAL must be between 0 and 3600 seconds')
        self.budget_limits = _configured_budgets()
        self._lock = threading.RLock()
        self._memory = {}
        self._failed = set()
        self._live = {}

    @staticmethod
    def _decode(state):
        if not isinstance(state, dict):
            raise ValueError('Invalid safety state')
        records = {}
        for provider, item in state.items():
            if provider not in PROVIDERS:
                raise ValueError('Unknown provider')
            # Upgrade the initial flat pause format without losing restrictions.
            if isinstance(item, str) and item in MESSAGES:
                item = {'pause': item}
            if not isinstance(item, dict) or set(item) - {'pause', 'pending', 'dispatched', 'next_at', 'next_http_at', 'usage'}:
                raise ValueError('Invalid provider record')
            pause, pending, dispatched, next_at = item.get('pause'), item.get('pending'), item.get('dispatched', False), item.get('next_at', 0)
            if (pause is not None and (not isinstance(pause, str) or pause not in MESSAGES)
                    or pending is not None and (not isinstance(pending, str) or len(pending) != 32)
                    or not isinstance(dispatched, bool)
                    or isinstance(next_at, bool) or not isinstance(next_at, (int, float))
                    or not math.isfinite(next_at) or next_at < 0):
                raise ValueError('Invalid provider state')
            if dispatched and pending is None:
                raise ValueError('Dispatch record lacks an attempt owner')
            next_http_at = item.get('next_http_at', 0)
            if (isinstance(next_http_at, bool) or not isinstance(next_http_at, (int, float))
                    or not math.isfinite(next_http_at) or next_http_at < 0):
                raise ValueError('Invalid HTTP pacing state')
            usage = item.get('usage', [])
            if (not isinstance(usage, list) or len(usage) > MAX_BUDGET
                    or any(not isinstance(entry, list) or len(entry) != 2
                           or entry[1] not in ('http', 'chat', 'generation')
                           or isinstance(entry[0], bool) or not isinstance(entry[0], (int, float))
                           or not math.isfinite(entry[0]) or entry[0] < 0 for entry in usage)):
                raise ValueError('Invalid usage ledger')
            records[provider] = dict(item)
        return records

    @contextlib.contextmanager
    def _state(self):
        with self._lock:
            if self.path is None:
                yield self._memory
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with self.path.with_suffix('.lock').open('a+') as lock:
                    os.chmod(lock.name, 0o600)
                    _file_lock(lock)
                    try:
                        state = self._decode(json.loads(self.path.read_text())) if self.path.exists() else {}
                        yield state
                    finally:
                        _file_lock(lock, unlock=True)
            except (OSError, ValueError, TypeError) as exc:
                raise ProviderRejected('safety_state_unavailable') from exc

    def _save(self, state):
        if self.path is None:
            return
        fd, temporary = tempfile.mkstemp(prefix='.provider-pauses-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                if hasattr(os, 'fchmod'):
                    os.fchmod(output.fileno(), 0o600)
                json.dump(state, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            if os.name != 'nt':
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def check(self, provider, owner=None, queued=False):
        if provider not in PROVIDERS:
            raise ValueError('Unknown provider')
        if provider in self._failed:
            raise ProviderRejected('safety_state_unavailable')
        active = current_attempt(provider)
        owner = owner or (active.id if active is not None and active.guard is self else None)
        with self._state() as state:
            record = state.get(provider, {})
            if record.get('pause'):
                raise ProviderRejected(record['pause'])
            if owner is not None and record.get('pending') != owner:
                raise ProviderRejected('upstream_failure')
            if (record.get('pending') and record['pending'] != owner
                    and not (queued and self._live.get(provider) == record['pending'])):
                raise ProviderRejected('upstream_failure')

    def begin(self, provider, cancelled=None):
        attempt = Attempt(self, provider, cancelled)
        self.check(provider)
        with self._state() as state:
            record = state.setdefault(provider, {})
            if record.get("pause") or record.get("pending"):
                raise ProviderRejected(record.get("pause") or "upstream_failure")
            record.update(pending=attempt.id, dispatched=False)
            try:
                self._save(state)  # Write-ahead: failure here prohibits all dispatch.
                self._live[provider] = attempt.id
            except OSError as exc:
                self._failed.add(provider)
                raise ProviderRejected('safety_state_unavailable') from exc
        return attempt

    @contextlib.contextmanager
    def attempt(self, provider, cancelled=None):
        attempt = self.begin(provider, cancelled)
        with attempt.bind():
            try:
                yield attempt
                attempt.complete()
            except BaseException as exc:
                attempt.abort(exc)
                raise

    def wait(self, provider, cancelled=None):
        # Compatibility helper; actual adapters use durable attempt ownership.
        with self.attempt(provider, cancelled) as attempt:
            attempt.dispatch()

    def reject(self, provider, exc, owner=None):
        error = exc if isinstance(exc, ProviderRejected) else rejection(
            exc, status=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None)
        try:
            with self._state() as state:
                if owner is not None and state.get(provider, {}).get('pending') != owner:
                    return ProviderRejected('upstream_failure')
                record = state.setdefault(provider, {})
                record.setdefault('pause', error.code)
                self._save(state)
                error = ProviderRejected(record['pause'])
        except (OSError, ProviderRejected):
            self._failed.add(provider)
            return ProviderRejected('safety_state_unavailable')
        return error

    def resume(self, provider):
        if provider not in PROVIDERS:
            raise ValueError('Unknown provider')
        with self._state() as state:
            record = state.setdefault(provider, {})
            record.pop('pause', None)
            record.pop('pending', None)
            record.pop('dispatched', None)
            self._save(state)
            self._failed.discard(provider)

    @staticmethod
    def _usage_revision(record):
        return hashlib.sha256(json.dumps(record.get('usage', []), separators=(',', ':')).encode()).hexdigest()

    def budget_status(self):
        now = time.time()
        with self._state() as state:
            record = state.get('deepseek', {})
            usage = record.get('usage', [])
            rows = []
            for window, category, limit in self.budget_limits:
                entries = sorted(t for t, kind in usage if t > now - window
                                 and (category is None or kind == category))
                needed = max(0, len(entries) + 1 - limit)
                rows.append({'category': category or 'http', 'window_seconds': window,
                             'used': len(entries), 'limit': limit,
                             'remaining': max(0, limit - len(entries)),
                             'next_available_at': entries[needed - 1] + window if needed else now})
            return {'provider': 'deepseek', 'at': now, 'budgets': rows,
                    'revision': self._usage_revision(record),
                    'paused': record.get('pause'), 'busy': bool(record.get('pending')),
                    'min_interval_seconds': self.interval}

    def reset_budget(self, revision):
        with self._state() as state:
            record = state.setdefault('deepseek', {})
            if record.get('pending'):
                raise RuntimeError('A request is active or unresolved; counters were not reset')
            if self._usage_revision(record) != revision:
                raise RuntimeError('Counters changed; inspect them again before resetting')
            # Only usage changes. Provider pauses and pacing survive this operation.
            record['usage'] = []
            self._save(state)

    def status(self):
        with self._state() as state:
            return {name: ('safety_state_unavailable' if name in self._failed else record.get('pause') or 'upstream_failure')
                    for name, record in state.items() if name in self._failed or record.get('pause') or record.get('pending')}


def _budget_usage(record, kind, now, http_cost=1, limits=None):
    usage = [entry for entry in record.get('usage', []) if entry[0] > now - 86400]
    # Local ceilings only, not provider-approved quotas.
    limits = _configured_budgets() if limits is None else limits
    for window, category, maximum in limits:
        if category is None or category == kind:
            count = sum(t > now - window and (category is None or k == category)
                        for t, k in usage)
            if count + (http_cost if category is None else 1) > maximum:
                error = ProviderRejected('local_budget_exceeded')
                error.before_dispatch = True
                raise error
    return usage


class Attempt:
    def __init__(self, guard, provider, cancelled):
        self.guard, self.provider, self.cancelled = guard, provider, cancelled
        self.id = uuid.uuid4().hex
        self.completed = False
        self.dispatched = False
        self.generation_dispatched = False
        self.challenge_auth_rejected = False

    @contextlib.contextmanager
    def bind(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)

    def check(self):
        if self.cancelled:
            self.cancelled()
        self.guard.check(self.provider, self.id)
        if self.cancelled:
            self.cancelled()

    def preflight_generation(self, new_chat):
        """Check the whole HTTP sequence before creating a chat or challenge."""
        self.check()
        if self.provider == 'deepseek':
            with self.guard._state() as state:
                record = state[self.provider]
                _budget_usage(record, 'generation', time.time(), 3 if new_chat else 2, self.guard.budget_limits)
                if new_chat:
                    _budget_usage(record, 'chat', time.time(), 3, self.guard.budget_limits)

    def dispatch(self, validate=None, kind="http"):
        if kind not in ('http', 'chat', 'generation'):
            raise ValueError('Unknown dispatch kind')
        while True:
            self.check()
            if validate is not None:
                validate()
            with self.guard._state() as state:
                record = state[self.provider]
                if record.get('pending') != self.id or record.get('pause'):
                    raise ProviderRejected(record.get('pause') or 'upstream_failure')
                delay = 0 if self.dispatched else record.get('next_at', 0) - time.time()
                if self.provider == 'deepseek':
                    delay = max(delay, record.get('next_http_at', 0) - time.time())
                if delay <= 0:
                    # Conservative even if persisting the dispatch later fails.
                    if kind == 'generation':
                        self.generation_dispatched = True
                    if self.provider == 'deepseek':
                        now = time.time()
                        usage = _budget_usage(record, kind, now, limits=self.guard.budget_limits)
                        record['usage'] = [*usage, [now, kind]]
                        record['next_http_at'] = now + min(self.guard.interval, 3)
                    if validate is not None:
                        validate()
                    if not self.dispatched:
                        record.update(dispatched=True, next_at=time.time() + self.guard.interval)
                        self.guard._save(state)
                        self.dispatched = True
                    else:
                        self.guard._save(state)
            if delay <= 0:
                # Saving/fsync and lock acquisition can block. Revoke the
                # authorization if cancellation or resume happened meanwhile.
                self.check()
                if validate is not None:
                    validate()
                return
            time.sleep(min(delay, 0.1))

    def complete(self):
        if self.completed:
            return
        with self.guard._state() as state:
            record = state[self.provider]
            if record.get('pending') != self.id:
                raise ProviderRejected('upstream_failure')
            record.pop('pending', None)
            record.pop('dispatched', None)
            self.guard._save(state)
            self.completed = True
            self.guard._live.pop(self.provider, None)

    def abort(self, exc=None):
        if self.completed:
            return
        if self.guard._live.get(self.provider) == self.id:
            self.guard._live.pop(self.provider, None)
        # Cancellation before dispatch is harmless; afterwards an unverified
        # outcome remains durably pending even if writing the pause fails.
        if self.dispatched or isinstance(exc, Exception) and not isinstance(exc, ValueError) and not getattr(exc, 'before_dispatch', False):
            self.guard.reject(self.provider, exc or ProviderRejected(), owner=self.id)
        else:
            try:
                self.complete()
            except OSError:
                self.guard._failed.add(self.provider)
            except ProviderRejected as error:
                if error.code == 'safety_state_unavailable':
                    self.guard._failed.add(self.provider)


guard = AccessGuard()


@contextlib.contextmanager
def provider_attempt(provider, cancelled=None):
    existing = current_attempt(provider)
    if existing is not None:
        existing.check()
        yield existing
    else:
        with guard.attempt(provider, cancelled) as attempt:
            yield attempt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Inspect pauses; resume only after manually verifying normal website access.')
    parser.add_argument('command', choices=('status', 'resume'))
    parser.add_argument('provider', nargs='?', choices=sorted(PROVIDERS))
    args = parser.parse_args()
    if args.command == 'resume':
        if not args.provider:
            parser.error('resume requires a provider')
        guard.resume(args.provider)
    print(json.dumps(guard.status(), sort_keys=True))
