"""Persistent, fail-closed provider pauses. Never stores upstream response data."""

import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time

import httpx

from settings import ROOT

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _file_lock(file, unlock=False):
    if os.name == "nt":
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX)

PROVIDERS = frozenset(("deepseek", "qwen", "grok", "mistral", "kimi", "glm", "gemini"))
MESSAGES = {
    "account_restricted": "The provider restricted this account. Stop and inspect the normal website.",
    "quota_exceeded": "The provider rejected the request because of a quota or usage limit.",
    "access_denied": "The provider denied access. Inspect sign-in, security and regional restrictions manually.",
    "session_expired": "The provider rejected the session. Sign in manually before resuming.",
    "upstream_failure": "The provider request failed or its outcome is uncertain. Automatic replay is disabled.",
    "safety_state_unavailable": "Cannot read the provider pause state. Requests are stopped until it is repaired.",
}


class ProviderRejected(RuntimeError):
    """A terminal error; HTTP 403 deliberately prevents standard SDK retries."""
    def __init__(self, code="upstream_failure"):
        self.code = code if code in MESSAGES else "upstream_failure"
        super().__init__(MESSAGES[self.code])


def rejection(detail=None, status=None):
    # Use upstream text only for classification, never in logs, responses or disk.
    text = str(detail).lower() if detail is not None else ""
    if "muted" in text or any(word in text for word in ("banned", "suspended", "account blocked")):
        return ProviderRejected("account_restricted")
    if status == 429 or any(word in text for word in ("quota", "rate_limit", "rate limit", "usage limit")):
        return ProviderRejected("quota_exceeded")
    if status == 403 or any(word in text for word in ("forbidden", "region", "security", "access denied")):
        return ProviderRejected("access_denied")
    if status == 401 or any(word in text for word in ("invalid_token", "token expired", "unauthorized")):
        return ProviderRejected("session_expired")
    return ProviderRejected()


class AccessGuard:
    def __init__(self, path=ROOT / "session" / "provider-pauses.json", interval=None):
        self.path = Path(path) if path is not None else None
        self.interval = float(os.getenv("PROVIDER_MIN_INTERVAL", "10") if interval is None else interval)
        if not math.isfinite(self.interval) or not 0 <= self.interval <= 3600:
            raise ValueError("PROVIDER_MIN_INTERVAL must be between 0 and 3600 seconds")
        self._lock = threading.RLock()
        self._memory = {}
        self._last = {}

    @contextlib.contextmanager
    def _state(self):
        with self._lock:
            if self.path is None:
                yield self._memory
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.path.with_suffix(".lock").open("a+") as lock:
                os.chmod(lock.name, 0o600)
                _file_lock(lock)
                try:
                    try:
                        state = json.loads(self.path.read_text()) if self.path.exists() else {}
                        if (not isinstance(state, dict) or any(
                                key not in PROVIDERS or not isinstance(value, str) or value not in MESSAGES
                                for key, value in state.items())):
                            raise ValueError("Invalid pause state")
                    except (ValueError, OSError) as exc:
                        raise ProviderRejected("safety_state_unavailable") from exc
                    yield state
                finally:
                    _file_lock(lock, unlock=True)

    def _save(self, state):
        if self.path is None:
            return
        fd, temporary = tempfile.mkstemp(prefix=".provider-pauses-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(state, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def check(self, provider):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider")
        try:
            with self._state() as state:
                code = self._memory.get(provider) or state.get(provider)
                if code:
                    raise ProviderRejected(code)
        except OSError as exc:
            raise ProviderRejected("safety_state_unavailable") from exc

    def wait(self, provider, cancelled=None):
        """Pace complete attempts, including tool continuations, not HTTP frames."""
        while True:
            self.check(provider)
            if cancelled is not None:
                cancelled()
            with self._lock:
                delay = self.interval - (time.monotonic() - self._last.get(provider, -math.inf))
                if delay <= 0:
                    self._last[provider] = time.monotonic()
                    return
            time.sleep(min(delay, 0.1))

    def reject(self, provider, exc):
        if isinstance(exc, ProviderRejected):
            error = exc
        elif isinstance(exc, httpx.HTTPStatusError):
            error = rejection(status=exc.response.status_code)
        else:
            error = rejection(exc)
        # Even a failed disk write must stop subsequent calls in this process.
        self._memory[provider] = error.code
        try:
            with self._state() as state:
                state.setdefault(provider, error.code)
                self._save(state)
                if self.path is not None:
                    # Persisted pauses are reread on every request, so a manual
                    # resume from a separate CLI process works without a restart.
                    self._memory.pop(provider, None)
                error = ProviderRejected(state[provider])
        except (OSError, ProviderRejected):
            self._memory[provider] = "safety_state_unavailable"
            return ProviderRejected("safety_state_unavailable")
        return error

    def resume(self, provider):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider")
        with self._state() as state:
            state.pop(provider, None)
            self._save(state)
            self._memory.pop(provider, None)

    def status(self):
        with self._state() as state:
            return {**state, **self._memory}


guard = AccessGuard()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect pauses; resume only after manually verifying normal website access.")
    parser.add_argument("command", choices=("status", "resume"))
    parser.add_argument("provider", nargs="?", choices=sorted(PROVIDERS))
    args = parser.parse_args()
    if args.command == "resume":
        if not args.provider:
            parser.error("resume requires a provider")
        guard.resume(args.provider)
    print(json.dumps(guard.status(), sort_keys=True))
