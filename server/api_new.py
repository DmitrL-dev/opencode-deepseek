"""TOOLCALL_LEGACY=0 tool-chat pipeline (demote, pointed repair, INVALID_MODE).

Only functions that differ from or did not exist in the main-era api.py path.
Callers in api.py dispatch here when TOOLCALL_LEGACY=0.
"""

from __future__ import annotations

import json
import os

from chat_protocol import Reply

from .diagnostics import save_tool_failure
from .openai_format import ToolCallError, looks_truncated, tool_catalog, _text_of
from .tool_notice import bridge_notice_payload, tool_failure_message
from .tool_protocol_new import demote_mistagged_tool_reply, validate_reply

_INVALID_MODE = os.getenv("TOOLCALL_INVALID_MODE", "error").strip().lower()
if _INVALID_MODE not in ("error", "text"):
    raise ValueError("TOOLCALL_INVALID_MODE must be 'error' or 'text'")

_BROWSER_REPAIR_TAIL = (
    "Send a call as a fenced tool_calls block: a JSON array of "
    "{\"name\": ..., \"arguments\": {...}} entries with exact tool names; all input "
    "fields belong inside arguments. If no action is needed, answer in plain text. "
    "Do not claim an unexecuted action succeeded."
)


def browser_repair_prompt(exc: ToolCallError, tools) -> str:
    """Pointed correction for every provider (no per-turn schema dump)."""
    if exc.kind == "unknown_tool":
        shown = f"`{exc.tool}` is not an available tool" if exc.tool else "it named no available tool"
        return (f"No tools were executed from your previous reply: {shown}. "
                "Available tools and their argument shapes (`?` = optional; use exactly these "
                "field names and nesting):\n" + tool_catalog(tools) + "\n" + _BROWSER_REPAIR_TAIL)
    if exc.kind in ("schema", "format") and exc.guidance:
        return exc.guidance + "\n" + _BROWSER_REPAIR_TAIL
    return ("No tools were executed from your previous reply: it was empty or its tool call "
            "was invalid.\n" + _BROWSER_REPAIR_TAIL)


def executed_calls(messages) -> list:
    """Canonical (name, arguments) of the calls whose results this request carries."""
    last = max((i for i, m in enumerate(messages) if m.role == "assistant"), default=None)
    if last is None or not messages[last].tool_calls:
        return []
    tail = messages[last + 1:]
    if not any(m.role == "tool" for m in tail):
        return []
    # A new, explicit user instruction is a new action boundary, e.g. checking
    # a formerly pending CI again. Durable history claims still prohibit replay
    # of the preceding request; equal arguments alone do not identify a replay.
    if any(m.role == "user" and _text_of(m.content).strip() for m in tail):
        return []
    out = []
    for call in messages[last].tool_calls:
        function = (call or {}).get("function") or {}
        out.append(canonical_call(function.get("name"), function.get("arguments")))
    return out


def canonical_call(name, arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)


def tool_chat_or_notice(prompt, req, model_type, provider: str, executed=()):
    """Validate; optional bridge notice under TOOLCALL_INVALID_MODE=text."""
    from . import api

    auto = req.tool_choice in (None, "auto")
    try:
        result = tool_chat_with_retry(prompt, req, model_type)
    except ToolCallError as exc:
        failed = exc.reply
        if (_INVALID_MODE != "text"
                or not auto or failed is None
                or failed.finish_reason != "stop" or not failed.conversation_id):
            raise
        print(f'[toolcalls] invalid reply returned as a bridge notice provider={provider}',
              flush=True)
        notice = bridge_notice_payload(exc)
        return (
            Reply(failed.text, failed.conversation_id, failed.finish_reason, notice),
            tool_failure_message(failed.text, exc),
            None,
        )
    reply, _content, calls = result
    if calls and executed and auto and reply.conversation_id:
        repeated = [canonical_call(c["function"]["name"], c["function"]["arguments"]) for c in calls]
        if any(call in executed for call in repeated):
            if _INVALID_MODE != "text":
                repeated_error = ToolCallError("Repeated tool call", kind="repeat")
                repeated_error.reply = reply
                raise repeated_error
            print(f'[toolcalls] repeated call not executed; returned as a bridge notice '
                  f'provider={provider}', flush=True)
            exc = ToolCallError("Repeated tool call", kind="repeat")
            notice = bridge_notice_payload(exc)
            return (
                Reply(reply.text, reply.conversation_id, reply.finish_reason, notice),
                tool_failure_message(reply.text, exc),
                None,
            )
    return result


def tool_chat_with_retry(prompt, req, model_type):
    """Demote + validate + pointed repair for every provider."""
    from . import api

    reply = api._chat_with_retry(prompt, req, model_type)
    if (reply.finish_reason == "stop"
            and req.tool_choice in (None, "auto")):
        demoted = demote_mistagged_tool_reply(reply.text, req.tools)
        if demoted is not None:
            print('[toolcalls] mistagged tool_calls demoted to text', flush=True)
            demoted_reply = Reply(demoted, reply.conversation_id, reply.finish_reason)
            demoted_reply.parts = [{"type": "text", "text": demoted}]
            return demoted_reply, demoted, None
    try:
        return validate_tool_reply(reply, req)
    except ToolCallError as initial_error:
        if not (api._FORMAT_REPAIR
                and req.tool_choice != "none" and reply.finish_reason == "stop"
                and reply.conversation_id):
            print('[toolcalls] format_correction=not_eligible', flush=True)
            raise
        api._check_cancelled()
        correction = req.model_copy(update={"conversation_id": reply.conversation_id})
        print('[toolcalls] format_correction=requested', flush=True)
        correction_prompt = browser_repair_prompt(initial_error, req.tools)
        corrected = api._chat_with_retry(correction_prompt, correction, None)
        if corrected.finish_reason != "stop":
            print('[toolcalls] format_correction=incomplete', flush=True)
            save_tool_failure(corrected, req)
            raise ToolCallError("The format correction did not finish normally; no calls were returned")
        try:
            result = validate_tool_reply(corrected, req)
        except ToolCallError as exc:
            print('[toolcalls] format_correction=invalid', flush=True)
            failure = ToolCallError(
                'The single format correction also failed; no further correction was requested. '
                + str(exc), repair_hint=exc.repair_hint, guidance=exc.guidance,
                kind=exc.kind, tool=exc.tool)
            failure.reply = exc.reply
            raise failure from exc
        print('[toolcalls] format_correction=validated', flush=True)
        return result


def validate_tool_reply(reply: Reply, req, *, allow_continuation=True):
    """LEGACY=0 validate + mid-message looks_truncated continuation."""
    from . import api
    from deepseek.auth import LoginRequired
    from providers.access import ProviderRejected

    names = api._tool_names(req.tools)
    maximum = api._MAX_CONTINUATIONS if allow_continuation else 0
    for attempt in range(maximum + 1):
        api._check_cancelled()
        can_continue = (reply.finish_reason == "length"
                        and looks_truncated(reply.text, names))
        if not can_continue:
            try:
                content, tool_calls, parts = validate_reply(
                    reply.text, req.tools, req.tool_choice,
                    finish_reason=reply.finish_reason,
                    provider=api.model_provider(req.model),
                    with_parts=True,
                )
                reply.parts = parts
            except ToolCallError as exc:
                exc.reply = reply
                save_tool_failure(reply, req)
                if api._toolcall_debug():
                    print(f"[toolcalls] invalid explicit reply:\n{reply.text}\n", flush=True)
                raise
            if tool_calls is None and api._toolcall_debug():
                print(f"[toolcalls] no call parsed; raw reply:\n{reply.text}\n", flush=True)
            return reply, content, tool_calls
        if req.tool_choice == "none":
            raise ToolCallError("The model started a tool call while tool_choice='none'")
        if attempt == maximum:
            raise ToolCallError("The provider reached its output limit before completing the tool reply; "
                                "no calls were returned and no further continuation is allowed")
        print(
            f"[toolcalls] reply truncated (attempt {attempt + 1}/"
            f"{api._MAX_CONTINUATIONS}); requesting continuation...",
            flush=True,
        )
        try:
            nxt = api._continue_reply(reply, req)
        except (LoginRequired, ProviderRejected):
            raise
        except Exception as e:
            raise ToolCallError("Could not finish the truncated tool reply") from e
        reply = Reply(text=reply.text + nxt.text, conversation_id=nxt.conversation_id,
                      finish_reason=nxt.finish_reason)
