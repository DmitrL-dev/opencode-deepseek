"""Offline regressions for the bridge's trust boundaries and protocol handling."""
import atexit
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The suite must observe the same configuration everywhere: CI, a fresh
# checkout and a configured developer machine. A local `.env` (created from
# `.env.example` per the README) or provider flags exported in the shell would
# silently change MODEL_MAP, tool-protocol modes and limits, so they are
# removed before the first bridge import. Keys are collected from
# `.env.example`, the real `.env` if it exists, the sources themselves and the
# provider families whose flag names are built dynamically.
_CONFIG_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)
# Settings the bridge reads directly. Scanning the sources keeps this list
# honest when a setting is missing from `.env.example` (RATE_LIMIT_PER_MINUTE,
# PORT, DSH_HOME, ...) and is cheaper to maintain than a hand-written copy.
_ENV_READS = (
    re.compile(r"\bgetenv\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"\benviron\.get\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"\benviron\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
)
_SOURCE_DIRS = ("server", "providers", "integrations", "deepseek", "qwen")
# Some flags are built dynamically (`provider.upper() + "_ENABLED"`), so they
# cannot be found by scanning. Clear those families wholesale.
CONFIG_PREFIXES = (
    "DEEPSEEK_", "QWEN_", "KIMI_", "GLM_", "GROK_", "MISTRAL_", "CHATGPT_",
    "CLAUDE_", "GEMINI_", "ANTIGRAVITY_", "BROWSER_", "PROVIDER_",
    "TOOLCALL_", "TOOL_INSTRUCTIONS_", "CONVERSATION_", "RATE_LIMIT_",
)


def configured_keys(*paths):
    """Setting names defined by the given dotenv-style files."""
    keys = set()
    for path in paths:
        try:
            keys.update(_CONFIG_KEY.findall(Path(path).read_text(encoding="utf-8")))
        except OSError:
            continue
    return keys


def _sourced_keys():
    """Setting names read through os.getenv / os.environ by bridge modules."""
    keys = set()
    sources = [ROOT / "settings.py", ROOT / "app.py", ROOT / "chat.py"]
    for name in _SOURCE_DIRS:
        sources.extend((ROOT / name).glob("*.py"))
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in _ENV_READS:
            keys.update(pattern.findall(text))
    return keys


def _install_environment_isolation():
    if "settings" in sys.modules:
        raise RuntimeError(
            "Test environment isolation must run before importing bridge modules")
    keys = configured_keys(ROOT / ".env.example", ROOT / ".env") | _sourced_keys()
    for key in keys:
        os.environ.pop(key, None)
    for key in [name for name in os.environ if name.startswith(CONFIG_PREFIXES)]:
        os.environ.pop(key, None)
    # server.config calls settings.load_environment() at import time, and many
    # regressions spawn subprocesses. This flag is honoured there and inherited,
    # so neither the test process nor a child reads the developer's `.env`.
    # Tests that verify dotenv loading remove it and supply their own root.
    os.environ["BRIDGE_SKIP_DOTENV"] = "1"


_install_environment_isolation()

# The /v1 API requires a private bearer token. Fixture clients act as the
# authorized local client: they get an isolated token file and fixture Host
# names. tests.test_crosscheck_fixes.ApiAuthBoundary tests unauthenticated refusals.
_AUTH_DIRECTORY = tempfile.TemporaryDirectory(prefix="bridge-api-auth-")
atexit.register(_AUTH_DIRECTORY.cleanup)
TEST_API_TOKEN = secrets.token_urlsafe(32)
_token_file = Path(_AUTH_DIRECTORY.name) / "api-token"
_token_file.write_text(TEST_API_TOKEN + "\n")
_token_file.chmod(0o600)
os.environ["BRIDGE_API_TOKEN_FILE"] = str(_token_file)
# Fixed, not setdefault: an exported API_ALLOWED_HOSTS must not change the
# fixture boundary either.
os.environ["API_ALLOWED_HOSTS"] = "testserver,audit.local,test.local,fixture.local,local.test"
os.environ.pop("API_AUTH", None)
NO_FIXTURE_AUTH = "x-test-no-fixture-auth"


def _authorize(request):
    if request.url.path.startswith("/v1") and "authorization" not in request.headers:
        if request.headers.get(NO_FIXTURE_AUTH) is None:
            request.headers["Authorization"] = "Bearer " + TEST_API_TOKEN
    request.headers.pop(NO_FIXTURE_AUTH, None)


def _install_fixture_auth():
    import httpx
    original_async = httpx.ASGITransport.handle_async_request

    async def handle_async_request(self, request):
        _authorize(request)
        return await original_async(self, request)

    httpx.ASGITransport.handle_async_request = handle_async_request
    try:
        from starlette import testclient
    except ImportError:
        return
    original_sync = testclient._TestClientTransport.handle_request

    def handle_request(self, request):
        _authorize(request)
        return original_sync(self, request)

    testclient._TestClientTransport.handle_request = handle_request


try:
    _install_fixture_auth()
except ImportError:  # Dependency-free subsets still import the package.
    pass
