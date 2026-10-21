"""TOOLCALL_LEGACY=0 reply contract: demote + multi-block validate for every provider.

Used only when TOOLCALL_LEGACY=0. Main-era validate lives in tool_protocol.py.
"""

import re
from typing import Optional

from .dsml import OPEN as DSML_OPEN, envelope as native_envelope
from .openai_format import (
    ToolCallError, enforce_tool_choice, find_tool_blocks, parse_tool_calls,
)

# Call-level "name" before "arguments" (not argument fields named "name").
# Comma optional so a missing-comma syntax error still exposes a known tool name.
_CALL_NAME_BEFORE_ARGS_RE = re.compile(
    r'"name"\s*:\s*"((?:\\.|[^"\\])*)"\s*,?\s*"arguments"')
_FUNCTION_NAME_RE = re.compile(
    r'"function"\s*:\s*\{\s*"name"\s*:\s*"((?:\\.|[^"\\])*)"')
_OPEN_TOOL_CALLS_FENCE_RE = re.compile(r'^([ \t]*```)tool_calls\b', re.MULTILINE)
_NATIVE_FRAME_RE = re.compile(r'(?m)^[ \t]*</?｜｜DSML｜｜ (?:calls|invoke|parameter)(?:\s|>)')


def _call_names_in_payload(body: str) -> list[str]:
    """Best-effort tool names from a fence body (valid or broken JSON)."""
    names = []
    for match in _CALL_NAME_BEFORE_ARGS_RE.finditer(body or ""):
        names.append(match.group(1))
    for match in _FUNCTION_NAME_RE.finditer(body or ""):
        names.append(match.group(1))
    return names


def _prose_around_tool_blocks(text: str, blocks) -> str:
    pieces, at = [], 0
    for start, end, _body, _closed in blocks:
        pieces.append(text[at:start])
        at = end
    pieces.append(text[at:])
    return "".join(pieces)


def _rewrite_tool_calls_fences_to_text(text: str, blocks) -> str:
    """Relabel top-level ```tool_calls fences as ```text (advice, not a call)."""
    result = text
    for start, end, _body, _closed in reversed(blocks):
        segment = result[start:end]
        rewritten, n = _OPEN_TOOL_CALLS_FENCE_RE.subn(r'\1text', segment, count=1)
        if n:
            result = result[:start] + rewritten + result[end:]
    return result


def demote_mistagged_tool_reply(text, tools, *, allow_dsml_preamble=False,
                                allow_dsml_wrapper_tolerances=False) -> Optional[str]:
    """If a reply is advice mistagged as tool_calls, return rewritten text.

    Demote (```tool_calls → ```text) when every top-level tool fence is either:
    - JSON (valid or broken) with no call-level ``name``, or
    - an unknown ``name`` plus accompanying prose outside the fences.

    Returns None when the reply should follow the normal tool validate path
    (real calls, schema/format errors, unknown tool with no prose, …).
    """
    text = text or ""
    if (text.strip().startswith(DSML_OPEN)
            or native_envelope(text, allow_preamble=allow_dsml_preamble,
                               allow_duplicate_open=allow_dsml_wrapper_tolerances) is not None):
        # A native parameter may itself contain example Markdown. Its bytes
        # belong to the argument, never to the advice-demotion layer.
        return None
    blocks = find_tool_blocks(text)
    if not blocks:
        return None
    names = {t.get("function", t).get("name") for t in tools or []}
    names.discard(None)
    has_prose = bool(_prose_around_tool_blocks(text, blocks).strip())
    saw_demote = False
    for _start, _end, body, _closed in blocks:
        wrapped = "```tool_calls\n" + body.strip("\r\n") + "\n```"
        try:
            _, block_calls = parse_tool_calls(
                wrapped, allowed_names=names,
                allow_closing_artifacts=True, allow_missing_array_closer=True,
                flat_argument_tools=tools)
        except ToolCallError as exc:
            if exc.kind == "no_name":
                saw_demote = True
                continue
            if exc.kind == "syntax":
                # A broken mixed wire packet is not advice. Never hide an
                # unfinished native invocation by relabelling its JSON fence.
                if _NATIVE_FRAME_RE.search(body):
                    return None
                extracted = _call_names_in_payload(body)
                if not extracted:
                    saw_demote = True
                    continue
                if has_prose and all(n not in names for n in extracted):
                    saw_demote = True
                    continue
                return None
            if exc.kind == "unknown_tool" and has_prose:
                saw_demote = True
                continue
            return None
        if block_calls:
            return None
    if not saw_demote:
        return None
    return _rewrite_tool_calls_fences_to_text(text, blocks)


def validate_reply(text, tools, tool_choice=None, *, finish_reason="stop", provider="deepseek",
                   with_parts=False, allow_dsml_preamble=False, allow_dsml_wrapper_tolerances=False):
    """LEGACY=0 validate: same multi-block contract for every provider.

    Apart from main's whole native DeepSeek envelope, only a top-level fenced
    ```tool_calls block can be a call; arrays without that fence, other fences
    and prose are plain text, wherever they stand. Advice
    mistagged as tool_calls is demoted in the API layer before this runs. Every
    remaining block must parse: syntax, no_name, unknown tool, or bad arguments
    fail the whole message. ``tool_choice=none`` never turns a block into an
    error. When every block is valid, all calls run in order and the text around
    them, with the call blocks cut out, is returned as content.

    With ``with_parts`` the reply also carries the original order as a list of
    ``{"type": "text", ...}`` / ``{"type": "tool_call", ...}`` items, so SSE can
    emit text and calls as separate frames instead of one flattened content.

    Whole native DSML replies retain main's strict provider/schema/finish policy.
    """
    text = text or ""
    if (text.strip().startswith(DSML_OPEN)
            or native_envelope(text, allow_preamble=allow_dsml_preamble,
                               allow_duplicate_open=allow_dsml_wrapper_tolerances) is not None):
        from .tool_protocol import validate_reply as validate_native
        content, calls = validate_native(text, tools, tool_choice,
                                        finish_reason=finish_reason, provider=provider,
                                        allow_dsml_preamble=allow_dsml_preamble,
                                        allow_dsml_wrapper_tolerances=allow_dsml_wrapper_tolerances)
        if with_parts:
            parts = [{'type': 'tool_call', 'call': call} for call in calls or []]
            return content, calls, parts
        return content, calls
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
        except ToolCallError:
            if tool_choice == "none":
                continue
            raise
        calls.extend(block_calls)
        spans.append((start, end, block_calls))
    if not calls:
        if not text.strip():
            raise ToolCallError("Provider returned no answer text or tool calls; no actions were returned")
        enforce_tool_choice(tool_choice, None)
        if with_parts:
            return text, None, [{"type": "text", "text": text}]
        return text, None
    enforce_tool_choice(tool_choice, calls)
    pieces, parts, at = [], [], 0
    for start, end, block_calls in spans:
        piece = text[at:start]
        pieces.append(piece)
        if piece.strip():
            parts.append({"type": "text", "text": piece.strip("\r\n")})
        for call in block_calls:
            parts.append({"type": "tool_call", "call": call})
        at = end
    piece = text[at:]
    pieces.append(piece)
    if piece.strip():
        parts.append({"type": "text", "text": piece.strip("\r\n")})
    content = "\n\n".join(piece.strip("\r\n") for piece in pieces if piece.strip())
    if with_parts:
        return content, calls, parts
    return content, calls
