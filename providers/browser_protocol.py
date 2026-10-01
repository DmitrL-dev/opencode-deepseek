"""Validate terminal replies produced by the sites' own frontend requests."""

import json
import struct

from chat_protocol import sse_events
from .common import ProviderUnavailable


def _object(payload):
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError):
        raise ProviderUnavailable("Unsupported browser response JSON") from None
    if not isinstance(value, dict) or value.get("error"):
        raise ProviderUnavailable("Browser provider rejected the completion")
    return value


def grok_answer(body):
    """Only a complete modelResponse, never an accumulated partial token tail."""
    final = None
    for line in body.splitlines():
        if not line.strip():
            continue
        frame = _object(line)
        result = frame.get("result")
        if not isinstance(result, dict) or result.get("error"):
            raise ProviderUnavailable("Unsupported Grok response frame")
        # New conversations wrap response; resumed conversations return it
        # directly. Both forms have the same terminal/error requirements.
        response = result.get("response", result)
        if not isinstance(response, dict) or response.get("error"):
            raise ProviderUnavailable("Grok rejected the completion")
        model = response.get("modelResponse")
        if model is not None:
            if (not isinstance(model, dict) or model.get("partial") is True
                    or model.get("error") or model.get("streamErrors")):
                raise ProviderUnavailable("Grok returned an incomplete answer")
            text = model.get("message")
            if not isinstance(text, str) or not text.strip():
                raise ProviderUnavailable("Grok returned no text answer")
            if final is not None:
                raise ProviderUnavailable("Grok returned ambiguous final answers")
            final = text
    if final is None:
        raise ProviderUnavailable("Grok disconnected before a complete modelResponse")
    return final


def glm_answer(body):
    text, done = "", False
    for event, payload in sse_events(body.decode("utf-8").splitlines()):
        if not payload or payload == "[DONE]":
            continue
        frame = _object(payload)
        if event == "error" or frame.get("type") == "error":
            raise ProviderUnavailable("GLM rejected the completion")
        if frame.get("type") != "chat:completion":
            continue
        data = frame.get("data")
        if not isinstance(data, dict) or data.get("error"):
            raise ProviderUnavailable("Invalid GLM completion frame")
        if data.get("scope", "legacy") != "legacy":
            continue
        # The v2 frontend uses a separate global terminal phase. It confirms
        # the accumulated answer, but must never import reasoning snapshots.
        if data.get("phase") == "done" and data.get("done") is True:
            if not text.strip():
                raise ProviderUnavailable("GLM completed without an answer phase")
            done = True
            continue
        # Reasoning/tool phases must not become executable tool-call text.
        if data.get("phase") in (None, "answer", "final"):
            if done and ("content" in data or "delta_content" in data):
                raise ProviderUnavailable("GLM sent answer content after completion")
            if "content" in data:
                if not isinstance(data["content"], str):
                    raise ProviderUnavailable("Invalid GLM answer content")
                text = data["content"]
            elif "delta_content" in data:
                if not isinstance(data["delta_content"], str):
                    raise ProviderUnavailable("Invalid GLM answer delta")
                text += data["delta_content"]
            if data.get("done") is True:
                done = True
    if not done or not text.strip():
        raise ProviderUnavailable("GLM disconnected before a complete text answer")
    return text


def connect_completed(body):
    """Connect RPC EOF alone is not success: require its end-stream envelope."""
    offset, messages, ended = 0, 0, False
    while offset < len(body):
        if len(body) - offset < 5 or ended:
            raise ProviderUnavailable("Invalid Connect response framing")
        flags, length = body[offset], struct.unpack_from(">I", body, offset + 1)[0]
        offset += 5
        if length > 8 * 1024 * 1024 or offset + length > len(body):
            raise ProviderUnavailable("Incomplete Connect response envelope")
        payload = body[offset:offset + length]
        offset += length
        if flags == 2:
            _object(payload)
            ended = True
        elif flags == 0:
            messages += 1
        else:
            raise ProviderUnavailable("Unsupported Connect response compression or flags")
    if not ended or not messages:
        raise ProviderUnavailable("Kimi disconnected before Connect completion")


def mistral_answer(body):
    text, done, ended = "", False, False
    for event, payload in sse_events(body.decode("utf-8").splitlines()):
        if payload == "[DONE]":
            if ended:
                raise ProviderUnavailable("Mistral returned duplicate stream endings")
            done = True
            ended = True
            continue
        if not payload:
            continue
        frame = _object(payload)
        kind = frame.get("type", event)
        if event == "error" or kind in ("error", "message.error"):
            raise ProviderUnavailable("Mistral rejected the completion")
        if kind in ("message.delta", "append-text"):
            if done:
                raise ProviderUnavailable("Mistral sent answer content after completion")
            value = frame.get("text", frame.get("content"))
            if not isinstance(value, str):
                raise ProviderUnavailable("Unsupported Mistral text delta")
            text += value
        elif kind in ("message.completed", "message.end", "complete"):
            if done:
                raise ProviderUnavailable("Mistral returned multiple completions")
            done = True
        elif "choices" in frame:
            choices = frame["choices"]
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ProviderUnavailable("Unsupported Mistral choices")
            choice = choices[0]
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise ProviderUnavailable("Invalid Mistral delta")
            value = delta.get("content", "")
            if not isinstance(value, str):
                raise ProviderUnavailable("Invalid Mistral text")
            if done and (value or choice.get("finish_reason") is not None):
                raise ProviderUnavailable("Mistral sent answer content after completion")
            text += value
            if choice.get("finish_reason") == "stop":
                done = True
            elif choice.get("finish_reason") is not None:
                raise ProviderUnavailable("Mistral answer was not completed")
    if not done or not text.strip():
        raise ProviderUnavailable("Unsupported or incomplete Mistral answer")
    return text
