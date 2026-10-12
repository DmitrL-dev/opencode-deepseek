"""Pure reply contract shared by live transport and offline replay.

No clients, session storage, diagnostics or network operations belong here.
Only complete, schema-checked batches may cross this boundary as actions.
"""

from .config import provider_uses_browser
from .openai_format import (
    ToolCallError, enforce_tool_choice, find_tool_blocks, has_tool_call_markup,
    parse_tool_calls,
)


def validate_reply(text, tools, tool_choice=None, *, finish_reason="stop", provider="deepseek"):
    if provider != "deepseek" and provider_uses_browser(provider):
        return _validate_browser_reply(text, tools, tool_choice, finish_reason)
    names = [t.get("function", t).get("name") for t in tools or []]
    finished = finish_reason == "stop"
    deepseek = finished and provider == "deepseek"
    content, calls = parse_tool_calls(
        text, allowed_names=names,
        allow_unclosed_fence=finished,
        allow_closing_artifacts=finished,
        allow_missing_array_closer=finished,
        # Incomplete replies get no normalization; their calls are rejected
        # below even if the JSON happens to be whole.
        flat_argument_tools=tools if finished else None,
        allow_outer_closer_transposition=deepseek,
        allow_missing_call_closer=deepseek,
        allow_tool_preamble=deepseek,
        allow_calls_fence=deepseek,
    )
    if (tool_choice != "none" and calls is None
            and has_tool_call_markup(text, names if provider == "deepseek" else None)):
        raise ToolCallError("Unsupported tool-call markup; no calls were returned")
    if not calls and not (content or "").strip():
        raise ToolCallError("Provider returned no answer text or tool calls; no actions were returned")
    enforce_tool_choice(tool_choice, calls)
    if calls and not finished:
        raise ToolCallError("The provider did not finish the tool reply normally; no calls were returned")
    return content, calls


def _validate_browser_reply(text, tools, tool_choice, finish_reason):
    """Browser providers: a message is an attempt at a tool call like in any agent.

    Only a top-level fenced ```tool_calls block can be a call; arrays without that
    fence, other fences and prose are plain text, wherever they stand. A block is
    text when its JSON is syntactically broken or names no tool (an abstract
    sample). Otherwise it must be fully valid: an unknown tool name or arguments
    that do not fit the tool's schema fail the whole message (nothing runs), so a
    caller can ask once for a correction or show the reply as text. When every
    block is valid, all calls run in order and the text around them, with the
    call blocks cut out, is returned as content.
    """
    text = text or ""
    names = [t.get("function", t).get("name") for t in tools or []]
    finished = finish_reason == "stop"
    blocks = find_tool_blocks(text)
    if blocks and not finished and tool_choice != "none":
        raise ToolCallError("The provider did not finish the tool reply normally; no calls were returned")
    calls, spans = [], []
    for start, end, body, _closed in blocks:
        wrapped = "```tool_calls\n" + body.strip("\r\n") + "\n```"
        try:
            _, block_calls = parse_tool_calls(
                wrapped, allowed_names=names,
                allow_closing_artifacts=True, allow_missing_array_closer=True,
                flat_argument_tools=tools)
        except ToolCallError as exc:
            if exc.kind in ("syntax", "no_name") or tool_choice == "none":
                continue
            raise
        calls.extend(block_calls)
        spans.append((start, end))
    if not calls:
        if not text.strip():
            raise ToolCallError("Provider returned no answer text or tool calls; no actions were returned")
        enforce_tool_choice(tool_choice, None)
        return text, None
    enforce_tool_choice(tool_choice, calls)
    pieces, at = [], 0
    for start, end in spans:
        pieces.append(text[at:start])
        at = end
    pieces.append(text[at:])
    content = "\n\n".join(piece.strip("\r\n") for piece in pieces if piece.strip())
    return content, calls
