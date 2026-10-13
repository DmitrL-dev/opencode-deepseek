"""Local notice for a rejected tool reply, delivered as a normal assistant message.

The agent shows the model's raw reply followed by a one-line notice and keeps the
chat open (a raised error would leave the provider link consumed). The next user
message carries the notice to the model. A notice names at most the offending
tool; it never lists tools or argument shapes and never echoes argument values.

API responses also carry a structured `bridge_notice` object and `message.refusal`
so the agent can treat the turn as a failed tool step without relying on prose alone.
"""

import re
from typing import Optional

from .openai_format import ToolCallError

NOTICE_PREFIX = "Bridge notice: "
STATUS_CODE = "BRIDGE_TOOL_NOT_EXECUTED"
_SEPARATOR = "\n\n" + NOTICE_PREFIX


def notice_of(text) -> Optional[str]:
    """The bridge notice at the end of an assistant message, or None."""
    if not isinstance(text, str):
        return None
    if text.startswith(NOTICE_PREFIX):
        return text
    at = text.rfind(_SEPARATOR)
    return text[at + 2:] if at >= 0 else None


def is_notice(text) -> bool:
    return notice_of(text) is not None


def _detail(exc: ToolCallError) -> str:
    tool = exc.tool
    if exc.kind == "unknown_tool":
        return f"`{tool}` is not an available tool." if tool else "it named no available tool."
    if exc.kind in ("schema", "format") and tool:
        return f"the arguments of `{tool}` do not match its schema."
    if exc.kind == "repeat":
        return "it repeats the call that was just executed, with the same arguments."
    return f"the reply was rejected: {exc}"


def tool_failure_notice(exc: ToolCallError) -> str:
    return (
        f"{NOTICE_PREFIX}{STATUS_CODE}: the tool call in the message above was NOT executed: "
        f"{_detail(exc)} "
        "(Local message: no tool ran. Send a follow-up, e.g. \"retry\", if the action "
        "is still needed.)"
    )


def tool_failure_message(raw_text: str, exc: ToolCallError) -> str:
    """The model's raw reply followed by the notice, as one assistant message."""
    notice = tool_failure_notice(exc)
    raw = (raw_text or "").rstrip()
    return f"{raw}\n\n{notice}" if raw else notice


def bridge_notice_payload(exc: ToolCallError) -> dict:
    """Structured failure for the agent (OpenAI-compatible extras on the completion).

    `as_tool_error` mirrors a tool-role error payload so agents can fold the turn into
    the usual tool-failure path without the bridge inventing executable tool_calls
    (those would be run locally or desync one-use history).
    """
    detail = _detail(exc)
    tool = exc.tool or None
    reason = f"NOT executed: {detail}"
    return {
        "executed": False,
        "code": STATUS_CODE,
        "kind": exc.kind,
        "tool": tool,
        "message": tool_failure_notice(exc),
        "as_tool_error": {
            "name": tool or "tool",
            "content": reason,
        },
    }


def provider_tool_error_followup(notice: str, user_text: str) -> str:
    """Format the next provider prompt like a failed tool result + user follow-up."""
    if STATUS_CODE in (notice or ""):
        named = re.search(r"`([^`]+)`", notice or "")
        name = named.group(1) if named else "tool"
        payload = f"Tool result ({name}): {notice}"
    else:
        payload = notice
    user = (user_text or "").strip()
    return f"{payload}\n\n{user}" if user else payload
