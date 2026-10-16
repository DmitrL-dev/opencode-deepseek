"""Opt-in per-conversation trace logging at the agent/provider boundary.

Trace files contain raw prompts and replies (including tool inputs/results), so
this feature is disabled by default and its output directory is git-ignored.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
SEPARATOR = {
    "from_agent": "------------ from agent---------------",
    "to_provider": "------------ to provider---------------",
    "from_provider": "------------ from provider---------------",
    "to_agent": "------------ to agent---------------",
    "add_chat": "-----------------------------add chat -------------------",
    "search_chat": "-----------------------Search chat--------------------------",
    "consume_chat": "-----------------------Consume chat--------------------------",
}
_lock = threading.RLock()


def enabled() -> bool:
    """Whether tracing is enabled; environment is read dynamically."""
    return os.getenv("TOOLCALL_LOG", "0").strip() == "1"


def _safe_provider(provider: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(provider or "unknown")).strip("._-")
    return (value or "unknown")[:64]


def _new_path(provider: str) -> Path | None:
    if not enabled():
        return None
    timestamp = datetime.now().strftime("%m%d%H%M%S")
    base = LOG_DIR / f"{timestamp}-{_safe_provider(provider)}.log"
    with _lock:
        try:
            LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                LOG_DIR.chmod(0o700)
            except OSError:
                pass
        except OSError:
            return None
        suffix = 1
        candidate = base
        while True:
            try:
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                try:
                    candidate.chmod(0o600)
                except OSError:
                    pass
                return candidate
            except FileExistsError:
                candidate = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
                suffix += 1
            except OSError:
                return None


def new_chat(provider: str) -> Path | None:
    """Create a trace file for a new provider conversation."""
    return _new_path(provider)


def _pretty_json(text: str) -> str:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return json.dumps(value, ensure_ascii=False, indent=2)


def _pretty_sse(text: str) -> str:
    """Pretty-print JSON after SSE data prefixes without changing JSON strings."""
    output = []
    cursor = 0
    pattern = re.compile(r"(?m)^([ \t]*data:[ \t]*)")
    while True:
        match = pattern.search(text, cursor)
        if not match:
            output.append(text[cursor:])
            break
        output.append(text[cursor:match.start()])
        prefix_start = match.start(1)
        value_start = match.end(1)
        output.append(match.group(1))
        if text.startswith("[DONE]", value_start):
            end = value_start + len("[DONE]")
            output.append("[DONE]")
            cursor = end
            continue

        decoder = json.JSONDecoder()
        try:
            value, end = decoder.raw_decode(text, value_start)
        except json.JSONDecodeError:
            # Not a JSON SSE data value: retain it verbatim.
            line_end = text.find("\n", value_start)
            if line_end < 0:
                output.append(text[value_start:])
                break
            output.append(text[value_start:line_end])
            cursor = line_end
            continue
        formatted = json.dumps(value, ensure_ascii=False, indent=2)
        formatted_lines = formatted.splitlines()
        output.append(formatted_lines[0])
        if len(formatted_lines) > 1:
            output.append("\n" + "\n".join(formatted_lines[1:]))
        cursor = end
    return "".join(output)


def _format_payload(text: str) -> str:
    formatted = _pretty_json(text)
    if formatted != text:
        return formatted
    if "data:" in text:
        return _pretty_sse(text)
    return text


def _payload_text(content) -> str:
    """Normalize boundary payloads into text without Python repr for JSON objects."""
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)

    # Starlette responses (JSONResponse, etc.) carry their serialized wire body.
    body = getattr(content, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return str(content)


def record(path: Path | str | None, direction: str, content) -> None:
    """Append one exact boundary payload, with JSON prettified for readability."""
    if not enabled():
        return
    if direction not in SEPARATOR:
        raise ValueError("Unknown tool-call trace direction")
    if path is None:
        path = new_chat("conversation-cache")
        if path is None:
            return
    text = _payload_text(content)
    text = _format_payload(text)
    block = f"{SEPARATOR[direction]}\n{text}"
    if not block.endswith("\n"):
        block += "\n"
    with _lock:
        try:
            with Path(path).open("a", encoding="utf-8", newline="") as stream:
                stream.write(block)
        except OSError:
            return


def record_error(path: Path | str | None, direction: str, error: BaseException) -> None:
    """Log an exception at the boundary where it is returned."""
    record(path, direction, f"{type(error).__name__}: {error}")
