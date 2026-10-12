"""Local notice for a rejected tool reply, delivered as a normal assistant message.

The agent shows the model's raw reply followed by a one-line notice and keeps the
chat open (a raised error would leave the provider link consumed). The next user
message carries the notice to the model. A notice names at most the offending
tool; it never lists tools or argument shapes and never echoes argument values.
"""

from typing import Optional

from .openai_format import ToolCallError

NOTICE_PREFIX = "Bridge notice: "
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
        f"{NOTICE_PREFIX}the tool call in the message above was NOT executed: {_detail(exc)} "
        "(Local message: no tool ran. Send a follow-up, e.g. \"retry\", if the action "
        "is still needed.)"
    )


def tool_failure_message(raw_text: str, exc: ToolCallError) -> str:
    """The model's raw reply followed by the notice, as one assistant message."""
    notice = tool_failure_notice(exc)
    raw = (raw_text or "").rstrip()
    return f"{raw}\n\n{notice}" if raw else notice
