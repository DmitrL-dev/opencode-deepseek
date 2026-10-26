"""Common transport types and SSE framing for the web-chat providers."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Reply:
    text: str
    conversation_id: str
    finish_reason: str = "stop"
    # Optional structured notice (agent-visible); no current path sets it.
    bridge_notice: Optional[dict] = None
    # Ordered text/tool_call parts for streaming; None keeps the legacy framing.
    parts: Optional[list] = None

    def __str__(self) -> str:
        return self.text


def sse_events(lines):
    event, data = "", []
    for line in lines:
        if not line:
            if data or event:
                yield event, "\n".join(data)
            event, data = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    if data or event:
        yield event, "\n".join(data)
