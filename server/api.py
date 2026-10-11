"""
OpenAI-compatible FastAPI server for DeepSeek and opt-in account providers.

Point any OpenAI client at http://localhost:8000/v1 :

    from openai import OpenAI
    client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")
    r = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "Hello!"}],
    )

Endpoints:
    GET  /v1/models
    POST /v1/chat/completions   (stream=true supported)
    GET  /healthz

Requests under /v1 are rate limited per client IP (default 30/min, set via
RATE_LIMIT_PER_MINUTE); /healthz is exempt.

All providers are opt-in. Sessions are loaded from cache only. Requests are
paced and attempted once; upstream failures persist a provider pause. Missing
sessions return 401; paused providers return 403 before any account access.
Streaming failures emit a terminal error and stop subsequent reconnects.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
import hashlib
from contextvars import ContextVar

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from deepseek.auth import SESSION_MAX_AGE, LoginRequired, get_session
from chat_protocol import Reply
from deepseek.client import DeepSeekClient, _decode_cid as decode_deepseek_cid
from qwen.auth import get_session as get_qwen_session
from qwen.renewal import renew_session as renew_qwen_session
from qwen.client import QwenClient, _decode_cid as decode_qwen_cid
from providers.conversations import decode as decode_provider_cid, PROVIDERS
from providers.registry import build_client as build_provider_client
from providers.access import guard, ProviderRejected, rejection
from .tool_protocol import validate_reply
from .diagnostics import save_tool_failure
from .conversation_cache import conversation_cache, ConversationStateError

from .config import (
    MODEL_MAP,
    RATE_LIMIT_PER_MINUTE,
    QWEN_RENEW_SESSION,
    REFRESH_BROWSER_CHANNEL,
    REFRESH_BROWSER_CHANNEL_FALLBACK,
    SERVER_INTERACTIVE_LOGIN,
    SESSION_REFRESH_ENABLED,
    SESSION_REFRESH_INTERVAL,
    advertised_model_ids,
    is_known_model,
    resolve_model_type,
    model_provider,
)
from .openai_format import (
    completion_response,
    looks_truncated,
    messages_to_prompt,
    sse_frames,
    stream_chunks,
    ToolCallError,
    validate_tool_choice,
)
from .ratelimit import RateLimiter, install_rate_limit
from .browser_routes import router as browser_router
from .schemas import ChatCompletionRequest

# One shared client (and its signed-in session) built lazily on first use.
_client: DeepSeekClient | None = None
_client_identity = None
_bound_deepseek = ContextVar("bound_deepseek", default=None)
_client_lock = threading.Lock()
_stop_refresh = threading.Event()
_request_gate = asyncio.Lock()
_qwen_request_gate = asyncio.Lock()
_qwen_client: QwenClient | None = None
_qwen_client_lock = threading.Lock()
_provider_request_gates = {name: asyncio.Lock() for name in PROVIDERS}

_MISSING = object()
_worker_cancellation = ContextVar("worker_cancellation", default=None)


def _check_cancellation(signal):
    if signal is not None and signal.is_set():
        raise asyncio.CancelledError()


def _check_cancelled():
    _check_cancellation(_worker_cancellation.get())


def _cancellation_check():
    # Browser lease routes run in another worker; retain the origin's Event.
    signal = _worker_cancellation.get()
    return lambda: _check_cancellation(signal)

def _tool_names(tools) -> set:
    """Extract requested tool names from OpenAI tool objects, defensively."""
    names = set()
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function", t)
        if isinstance(fn, dict) and isinstance(fn.get("name"), str) and fn["name"]:
            names.add(fn["name"])
    return names


def _debug_toolcalls() -> bool:
    return os.getenv("DEBUG_TOOLCALLS", "").strip().lower() not in (
        "", "0", "false", "no", "off",
    )


def _build_client(force: bool = False, allow_interactive=None, session=None) -> DeepSeekClient:
    """Load a cached session only; legacy force/login arguments cannot browse."""
    if session is None:
        session = get_session(max_age=SESSION_MAX_AGE, allow_interactive=False, allow_refresh=False)
    return DeepSeekClient(session=session, access_guard=None)  # server owns pause/pacing


def get_client(force_refresh: bool = False, rejected_client=None) -> DeepSeekClient:
    """Cache a client; an explicit reset reloads the session file without browsing."""
    global _client, _client_identity
    # One parsed snapshot supplies both credentials and identity. Never hash
    # the current file separately from the Session used to build the client.
    session = get_session(max_age=SESSION_MAX_AGE, allow_interactive=False, allow_refresh=False)
    identity = _session_identity(session)
    with _client_lock:
        if _client_identity != identity or _client is None or (force_refresh and (
            rejected_client is None or _client is rejected_client
        )):
            _client = _build_client(force=force_refresh, session=session)
            _client_identity = identity
        return _client


def _session_identity(session):
    return session.identity()


def reset_client() -> None:
    """Drop the cached client so the next get_client() rebuilds the session.

    The old client is intentionally not closed synchronously: an in-flight
    request may still hold a reference to it. Leaking one httpx pool is
    negligible; closing it early would break that request."""
    global _client
    with _client_lock:
        _client = None


def get_qwen_client(force_refresh=False, rejected_client=None) -> QwenClient:
    """Load local Qwen credentials, with separately opted-in pre-dispatch renewal."""
    global _qwen_client
    with _qwen_client_lock:
        # A visible auth window can save a rotated token before the previous
        # token expires. Read the atomic local snapshot on every new request;
        # never keep using an older in-memory credential after that handoff.
        try:
            session = get_qwen_session(allow_interactive=False, allow_refresh=False,
                                       channel=REFRESH_BROWSER_CHANNEL,
                                       fallback_channel=REFRESH_BROWSER_CHANNEL_FALLBACK)
        except LoginRequired:
            if not QWEN_RENEW_SESSION:
                raise
            session = renew_qwen_session()
        if (_qwen_client is None or _qwen_client.session != session
                or (force_refresh and (rejected_client is None or _qwen_client is rejected_client))):
            # Existing requests retain their own client; do not close it here.
            _qwen_client = QwenClient(session, access_guard=None)  # server owns pause/pacing
        return _qwen_client


def _request_client(req, force_refresh=False, rejected_client=None):
    provider = model_provider(req.model)
    guard.check(provider)
    if provider in PROVIDERS:
        # Browser routes run in another worker context. Bind the originating
        # request's Event now so they see cancellation before this worker wakes.
        signal = _worker_cancellation.get()
        return build_provider_client(provider, lambda: _check_cancellation(signal))
    factory = get_qwen_client if provider == "qwen" else get_client
    if force_refresh:
        return factory(True, rejected_client=rejected_client)
    return factory()


def _request_queue(req):
    provider = model_provider(req.model)
    if provider in PROVIDERS:
        return _provider_request_gates[provider]
    return _qwen_request_gate if provider == "qwen" else _request_gate


async def _run_worker(function, *args):
    """Cancellation waits for sync work rather than abandoning a live request."""
    signal = threading.Event()

    def run():
        token = _worker_cancellation.set(signal)
        try:
            _check_cancelled()
            return function(*args)
        finally:
            _worker_cancellation.reset(token)

    worker = asyncio.create_task(run_in_threadpool(run))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        signal.set()
        # Also shield against Starlette's AnyIO disconnect cancellation scope.
        with anyio.CancelScope(shield=True):
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
        raise


async def _run_queued(function, *args, gate=None):
    """Keep one account request, including retries/continuations, in the queue."""
    queue = gate if gate is not None else _request_gate
    async with queue:
        return await _run_worker(function, *args)


async def _run_chat_with_retry(prompt: str, req: ChatCompletionRequest, model_type):
    """Run one paced completion inside the provider queue."""
    return await _run_queued(_chat_with_retry, prompt, req, model_type, gate=_request_queue(req))


def _invalidate_client(provider, client=None):
    global _client, _qwen_client
    lock = _qwen_client_lock if provider == "qwen" else _client_lock
    if provider in ("deepseek", "qwen"):
        with lock:
            if provider == "deepseek" and (client is None or _client is client):
                _client = None
            elif provider == "qwen" and (client is None or _qwen_client is client):
                _qwen_client = None


def _chat_with_retry(prompt: str, req: ChatCompletionRequest, model_type):
    """One durable attempt; cancellation cannot erase an uncertain dispatch."""
    provider = model_provider(req.model)
    if provider == 'deepseek' and len(prompt.encode('utf-8')) > 131072:
        raise ProviderRejected('local_budget_exceeded')
    client = None
    attempt = None
    try:
        with guard.attempt(provider, _cancellation_check()) as attempt:
            binding = _bound_deepseek.get() if provider == 'deepseek' else None
            if binding is not None:
                binding['attempts'].append(attempt)
            client = binding['client'] if binding is not None else _request_client(req)
            attempt.check()  # Recheck a pause that happened while building a client.
            return client.chat(prompt, req.conversation_id, model_type, req.thinking, req.search)
    except LoginRequired:
        _invalidate_client(provider, client)
        raise
    except Exception as exc:
        # A contender that never owned an attempt cannot mutate its peer state.
        if attempt is not None:
            _invalidate_client(provider, client)
        error = exc if isinstance(exc, ProviderRejected) else rejection(exc)
        raise error from exc


# A truncated tool reply is continued this many times before giving up. Each
# round-trip resumes the same provider conversation and appends the next slice.
_MAX_CONTINUATIONS = int(os.getenv("TOOLCALL_MAX_CONTINUATIONS", "0"))
if _MAX_CONTINUATIONS < 0:
    raise ValueError("TOOLCALL_MAX_CONTINUATIONS must be non-negative")

# One opt-in format correction in the same DeepSeek conversation. This is
# separate from transport retries and output-limit continuation; neither a
# rejection nor an uncertain/truncated generation qualifies.
_FORMAT_REPAIR = os.getenv("TOOLCALL_FORMAT_REPAIR", "0") == "1"
_FORMAT_REPAIR_PROMPT = (
    "No tools were executed from your previous reply: it was empty or its tool-call format was invalid. "
    "Choose the next response using the task and tool schemas already provided. "
    "If a tool is necessary, return ONLY one fenced tool_calls block containing a "
    "complete valid JSON array. Each entry must have name and arguments; ALL input "
    "fields belong inside the arguments object. Escape string values correctly. "
    "No commentary before or after the block, and no XML/DSML tags. "
    "If you intended only an explanation or the work is complete, give plain text "
    "without a tool_calls block. Do not claim an unexecuted action succeeded."
)

# Sent to make the model finish an answer its output limit cut off.
_CONTINUE_PROMPT = (
    "Your previous message was cut off by the output limit before it finished. "
    "Continue EXACTLY from the character where it stopped. Output only the "
    "remaining characters — no preamble, no apology, no repetition, and no "
    "opening code fence (only the missing closing one, if that is what was cut)."
)


def _continue_reply(prev: Reply, req: ChatCompletionRequest):
    """Resume the same provider conversation and return the next slice.

    On resume the thread keeps its own model, so `model` is passed as None —
    matching both clients' resume rules."""
    if not prev.conversation_id:
        raise ToolCallError("Cannot continue a tool reply without a conversation_id")
    continuation = req.model_copy(update={"conversation_id": prev.conversation_id})
    return _chat_with_retry(_CONTINUE_PROMPT, continuation, None)


async def _run_tool_chat(prompt: str, req: ChatCompletionRequest, model_type):
    return await _run_queued(_conversation_tool_chat, prompt, req, model_type, gate=_request_queue(req))


def _plain_conversation_reply(prompt, req, model_type):
    reply = _chat_with_retry(prompt, req, model_type)
    return reply, reply.text, None


def _conversation_tool_chat(prompt, req, model_type):
    if model_provider(req.model) != "deepseek":
        return _tool_chat_with_retry(prompt, req, model_type)
    original = req
    claim = {}
    binding = {'attempts': []}
    token = None
    succeeded = False
    try:
        guard.check('deepseek')
        _check_cancelled()
        client = get_client()
        # Test caches deliberately have no account file. Production scopes
        # derive only from this pinned client's Session, never a later file.
        scope = ('isolated' if conversation_cache.account_file is None else
                 'session-v2:' + (client.session.continuation_scope or _session_identity(client.session)))
        binding['client'] = client
        token = _bound_deepseek.set(binding)
        req, steps = conversation_cache.prepare(req, scope=scope, claim=claim)
        if req is not original:
            prompt = messages_to_prompt(req.messages, req.tools, req.tool_choice)
            model_type = None
        result = (_tool_chat_with_retry(prompt, req, model_type) if req.tools else
                  _plain_conversation_reply(prompt, req, model_type))
        conversation_cache.remember(original, *result, steps, scope=scope)
        succeeded = True
        return result
    except ConversationStateError as exc:
        raise ToolCallError(str(exc)) from exc
    finally:
        if token is not None:
            _bound_deepseek.reset(token)
        # An explicit challenge auth rejection is also pre-generation, but an
        # earlier generation (e.g. format correction) must keep the claim used.
        # The provider pause/pending state and all charges remain unchanged.
        if not succeeded and all(
            not a.dispatched or (a.challenge_auth_rejected and not a.generation_dispatched)
            for a in binding['attempts']
        ):
            conversation_cache.release(claim)


def _tool_chat_with_retry(prompt: str, req: ChatCompletionRequest, model_type):
    reply = _chat_with_retry(prompt, req, model_type)
    try:
        return _validate_tool_reply(reply, req)
    except ToolCallError as initial_error:
        if not (_FORMAT_REPAIR and model_provider(req.model) == "deepseek"
                and req.tool_choice != "none" and reply.finish_reason == "stop"
                and reply.conversation_id):
            print('[toolcalls] format_correction=not_eligible', flush=True)
            raise
        _check_cancelled()
        correction = req.model_copy(update={"conversation_id": reply.conversation_id})
        print('[toolcalls] format_correction=requested', flush=True)
        correction_prompt = _FORMAT_REPAIR_PROMPT
        if initial_error.repair_hint:
            correction_prompt += "\n" + initial_error.repair_hint
        corrected = _chat_with_retry(correction_prompt, correction, None)
        if corrected.finish_reason != "stop":
            print('[toolcalls] format_correction=incomplete', flush=True)
            save_tool_failure(corrected, req)
            raise ToolCallError("The format correction did not finish normally; no calls were returned")
        # No recursion: a second invalid answer is terminal. The original
        # batch never escaped validation and cannot be executed twice.
        try:
            result = _validate_tool_reply(corrected, req)
        except ToolCallError as exc:
            print('[toolcalls] format_correction=invalid', flush=True)
            raise ToolCallError(
                'The single format correction also failed; no further correction was requested. '
                + str(exc)
            ) from exc
        print('[toolcalls] format_correction=validated', flush=True)
        return result


def _validate_tool_reply(reply: Reply, req: ChatCompletionRequest):
    """Validate the whole reply; continuation requires opt-in and an output limit.

    A missing Markdown fence alone is not evidence of an output limit. A
    normally completed response may omit the fence or exactly the outer array
    closer only when all call objects and their arguments validate in full.
    """
    names = _tool_names(req.tools)

    for attempt in range(_MAX_CONTINUATIONS + 1):
        _check_cancelled()
        can_continue = (reply.finish_reason == "length"
                        and looks_truncated(reply.text, names))
        if not can_continue:
            try:
                content, tool_calls = validate_reply(
                    reply.text, req.tools, req.tool_choice,
                    finish_reason=reply.finish_reason,
                    provider=model_provider(req.model),
                )
            except ToolCallError:
                save_tool_failure(reply, req)
                if _debug_toolcalls():
                    print(f"[toolcalls] invalid explicit reply:\n{reply.text}\n", flush=True)
                raise
            if tool_calls is None and _debug_toolcalls():
                print(f"[toolcalls] no call parsed; raw reply:\n{reply.text}\n", flush=True)
            return reply, content, tool_calls
        if req.tool_choice == "none":
            raise ToolCallError("The model started a tool call while tool_choice='none'")
        if attempt == _MAX_CONTINUATIONS:
            raise ToolCallError("The provider reached its output limit before completing the tool reply; "
                                "no calls were returned and no further continuation is allowed")
        print(
            f"[toolcalls] reply truncated (attempt {attempt + 1}/"
            f"{_MAX_CONTINUATIONS}); requesting continuation...",
            flush=True,
        )
        try:
            nxt = _continue_reply(reply, req)
        except (LoginRequired, ProviderRejected):
            raise
        except Exception as e:
            raise ToolCallError("Could not finish the truncated tool reply") from e
        reply = Reply(text=reply.text + nxt.text, conversation_id=nxt.conversation_id,
                      finish_reason=nxt.finish_reason)


def _open_stream(client: DeepSeekClient, prompt: str, req: ChatCompletionRequest, model_type):
    return client.stream(
        prompt, conversation_id=req.conversation_id,
        model=model_type, thinking=req.thinking, search=req.search,
    )


def _sse_error(message: str, err_type: str = "server_error") -> str:
    obj = {"error": {"message": message, "type": err_type, "code": err_type, "retryable": False}}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\ndata: [DONE]\n\n"


# How often to emit an SSE keep-alive comment while a buffered tool reply is
# still generating. Must stay below the client's idle read timeout.
_KEEPALIVE_SECONDS = 10.0


async def _tool_stream(req: ChatCompletionRequest, prompt: str, model_type):
    """Stream a buffered tool-call reply without tripping the client's timeout.

    Tool calls are emulated by buffering the whole reply before parsing, which
    can take minutes for a large write/edit. We run that work as a task and emit
    SSE keep-alive comments while it runs so the client's idle timer never fires.
    """
    task = asyncio.ensure_future(_run_tool_chat(prompt, req, model_type))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_KEEPALIVE_SECONDS)
            if task in done:
                break
            yield ": keep-alive\n\n"

        try:
            reply, content, tool_calls = task.result()
        except LoginRequired as e:
            yield _sse_error(str(e), "login_required")
            return
        except ToolCallError as e:
            yield _sse_error(str(e), "invalid_tool_response")
            return
        except ProviderRejected as e:
            yield _sse_error(str(e), e.code)
            return
        except Exception:
            yield _sse_error("Provider request failed; automatic replay is disabled.")
            return

        for frame in sse_frames(req.model, content, tool_calls, reply.conversation_id,
                                finish_reason=reply.finish_reason):
            yield frame
    finally:
        if not task.done():
            task.cancel()
        # A cancelled buffered request retains its worker until HTTP finishes.
        # Retrieve any eventual exception even after the SSE consumer leaves.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())


def _stream_with_retry(client: DeepSeekClient, prompt: str,
                       req: ChatCompletionRequest, model_type):
    iterator = None
    attempt = None
    try:
        attempt = guard.begin(model_provider(req.model), _cancellation_check())
        if client is None:
            with attempt.bind():
                client = _request_client(req)
        attempt.check()
        stream = _open_stream(client, prompt, req, model_type)
        stream.access_attempt = attempt
        iterator = iter(stream)
        first = next(iterator, _MISSING)

        def remaining():
            if first is not _MISSING:
                yield first
            yield from iterator
            # Verify and clear the journal before yielding final SSE frames.
            attempt.complete()

        yield from stream_chunks(req.model, stream, iterator=remaining())
    except LoginRequired as exc:
        _invalidate_client(model_provider(req.model), client)
        yield _sse_error(str(exc), "login_required")
    except Exception as exc:
        if attempt is not None:
            attempt.abort(exc)
            _invalidate_client(model_provider(req.model), client)
        error = exc if isinstance(exc, ProviderRejected) else rejection(exc)
        yield _sse_error(str(error), error.code)
    finally:
        if iterator is not None and hasattr(iterator, "close"):
            iterator.close()
        if attempt is not None:
            attempt.abort()


async def _plain_stream(client, prompt, req, model_type):
    # Queue asynchronously so waiting requests cannot exhaust worker threads
    # needed to advance an already-open stream.
    async with _request_queue(req):
        generator = _stream_with_retry(client, prompt, req, model_type)
        try:
            while True:
                frame = await _run_worker(next, generator, _MISSING)
                if frame is _MISSING:
                    break
                yield frame
        finally:
            with anyio.CancelScope(shield=True):
                await _run_worker(generator.close)


async def _buffered_plain_stream(req, prompt, model_type):
    """Keep the connection alive while optional transports validate an answer."""
    task = asyncio.ensure_future(_run_chat_with_retry(prompt, req, model_type))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_KEEPALIVE_SECONDS)
            if task in done:
                break
            yield ": keep-alive\n\n"
        try:
            reply = task.result()
        except LoginRequired as exc:
            yield _sse_error(str(exc), "login_required")
            return
        except ProviderRejected as exc:
            yield _sse_error(str(exc), exc.code)
            return
        except Exception:
            yield _sse_error("Provider request failed; automatic replay is disabled.")
            return
        for frame in sse_frames(req.model, reply.text, None, reply.conversation_id,
                                finish_reason=reply.finish_reason):
            yield frame
    finally:
        if not task.done():
            task.cancel()
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _request_gate, _qwen_request_gate, _stop_refresh, _provider_request_gates
    _request_gate = asyncio.Lock()
    _qwen_request_gate = asyncio.Lock()
    _provider_request_gates = {name: asyncio.Lock() for name in PROVIDERS}
    _stop_refresh = threading.Event()
    stop_event = _stop_refresh
    # Background browser recapture is retired, including existing .env opt-ins.
    try:
        yield
    finally:
        stop_event.set()


app = FastAPI(title="Account providers OpenAI-compatible API", version="0.2.0", lifespan=lifespan)
app.include_router(browser_router)
from server.budgets import router as budget_router
app.include_router(budget_router)
from server.session_transfer import router as session_transfer_router
app.include_router(session_transfer_router)
install_rate_limit(app, RateLimiter(limit=RATE_LIMIT_PER_MINUTE, window=60.0))


def _error(message: str, status: int = 500, err_type: str = "server_error"):
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": err_type, "retryable": False}},
        headers={"x-should-retry": "false"},
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/v1/models")
def list_models():
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": created, "owned_by": model_provider(name)}
            for name in advertised_model_ids()
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request = None):
    # StreamingResponse owns disconnect monitoring after headers. Direct Python
    # callers do not have an ASGI transport to monitor.
    if request is None or req.stream:
        return await _chat_completions(req)
    stopping = asyncio.Event()
    async def disconnected():
        # The body has already been parsed by FastAPI. Await the next ASGI
        # event instead of polling is_disconnected: its immediately-cancelled
        # scope can lose disconnects behind BaseHTTPMiddleware task groups.
        while not stopping.is_set():
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return
    work = asyncio.create_task(_chat_completions(req))
    watch = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
        if watch in done:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            return _error("Client disconnected", status=499, err_type="client_disconnected")
        return await work
    finally:
        # Stop and join both tasks, including through the middleware's
        # cancellation scopes. No orphaned receive or provider worker remains.
        stopping.set()
        watch.cancel()
        if not work.done():
            work.cancel()
        await asyncio.gather(work, watch, return_exceptions=True)


async def _chat_completions(req: ChatCompletionRequest):
    if not req.messages:
        return _error("`messages` must not be empty", status=400, err_type="invalid_request_error")

    if not is_known_model(req.model):
        return _error(
            f"The model `{req.model}` does not exist. Available models: "
            f"{', '.join(advertised_model_ids())}",
            status=404, err_type="model_not_found",
        )

    try:
        # A live request in this process may be queued behind; abandoned or
        # external attempts and real pauses must still fail before any factory.
        await _run_worker(guard.check, model_provider(req.model), None, True)
    except ProviderRejected as exc:
        return _error(str(exc), status=403, err_type=exc.code)

    if req.conversation_id:
        try:
            provider = model_provider(req.model)
            if provider in PROVIDERS:
                decode_provider_cid(req.conversation_id, provider)
            elif provider == "qwen":
                decode_qwen_cid(req.conversation_id)
            else:
                decode_deepseek_cid(req.conversation_id)
        except ValueError as exc:
            return _error(str(exc), status=400, err_type="invalid_request_error")

    try:
        names = _tool_names(req.tools)
        if req.tools and len(names) != len(req.tools):
            raise ValueError("Tools must have distinct, non-empty function names")
        validate_tool_choice(req.tool_choice, names)
    except ValueError as exc:
        return _error(str(exc), status=400, err_type="invalid_request_error")

    # A thread's model is fixed when it's created, so on resume we ignore `model`
    # (the OpenAI SDK always sends one) and let the existing thread's model stand.
    model_type = None if req.conversation_id else resolve_model_type(req.model)
    prompt = messages_to_prompt(req.messages, req.tools, req.tool_choice)

    # Tool calls are emulated by buffering the reply and parsing it, so we use the
    # non-streaming call and then emit SSE frames when the client asked to stream.
    # The provider worker latches upstream failures before exposing the result.
    if req.tools or model_provider(req.model) == "deepseek":
        if req.stream:
            return StreamingResponse(
                _tool_stream(req, prompt, model_type),
                media_type="text/event-stream",
            )
        try:
            reply, content, tool_calls = await _run_tool_chat(prompt, req, model_type)
        except LoginRequired as e:
            return _error(str(e), status=401, err_type="login_required")
        except ToolCallError as e:
            return _error(str(e), status=400, err_type="invalid_tool_response")
        except ProviderRejected as e:
            return _error(str(e), status=403, err_type=e.code)
        except Exception:
            return _error("Provider request failed; automatic replay is disabled.", status=400)

        return completion_response(
            req.model, content, prompt, reply.conversation_id, tool_calls,
            finish_reason=reply.finish_reason,
        )

    # Plain chat: one incremental stream or one buffered completion.
    if req.stream:
        if model_provider(req.model) in PROVIDERS:
            return StreamingResponse(_buffered_plain_stream(req, prompt, model_type),
                                     media_type="text/event-stream")
        return StreamingResponse(_plain_stream(None, prompt, req, model_type),
                                 media_type="text/event-stream")

    try:
        reply = await _run_chat_with_retry(prompt, req, model_type)
    except LoginRequired as e:
        return _error(str(e), status=401, err_type="login_required")
    except ProviderRejected as e:
        return _error(str(e), status=403, err_type=e.code)
    except Exception:
        return _error("Provider request failed; automatic replay is disabled.", status=400)

    return completion_response(req.model, reply.text, prompt, reply.conversation_id,
                               finish_reason=reply.finish_reason)
