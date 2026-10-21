"""Strict, inert decoder for a complete native DeepSeek tool envelope.

Not XML: no entity expansion, external resources, escaping, or tag recovery.
This decoder returns data only; the shared tool parser owns schema validation.
"""
import re

OPEN = '<｜｜DSML｜｜ calls>'
CLOSE = '</｜｜DSML｜｜ calls>'
INVOKE_END = '</｜｜DSML｜｜ invoke>'
PARAMETER_END = '</｜｜DSML｜｜ parameter>'
_NAME = r'([^"<>\x00-\x1f]{1,256})'
_INVOKE = re.compile(r'<｜｜DSML｜｜ invoke name="' + _NAME + r'">')
_PARAMETER = re.compile(r'<｜｜DSML｜｜ parameter name="' + _NAME + r'" string="(true|false)">')
_SPACE = re.compile(r'[ \t\r\n]*')


def envelope(text, *, allow_preamble=False):
    """Locate one explicit native action envelope, never quoted/example code.

    Plain commentary is eligible only when the caller explicitly requested the
    native dialect. This selects a suffix, not a decoder tolerance: the entire
    suffix still has to pass decode() and every advertised argument schema.
    """
    stripped = (text or '').strip()
    if stripped.startswith(OPEN):
        first_line = next((line for line in text.splitlines() if line.strip()), '')
        # Markdown indentation is data, even for otherwise valid native tags.
        return stripped if first_line.startswith(OPEN) else None
    if not allow_preamble:
        return None
    starts = list(re.finditer(r'(?m)^' + re.escape(OPEN) + r'[ \t]*\r?$', text or ''))
    if len(starts) != 1:
        return None
    prefix = text[:starts[0].start()]
    if (any(marker in prefix for marker in ('`', '~~~', '<', '\t'))
            or re.search(r'(?m)^(?:[ \t]*>| {4})', prefix)
            or prefix.rstrip().endswith(':')):
        return None
    return text[starts[0].start():].rstrip()


def decode(text, load_json):
    """Decode one whole batch without changing or inferring argument values."""
    if len(text.encode()) > 4 * 1024 * 1024 or not text.startswith(OPEN):
        raise ValueError('Invalid DSML envelope')
    position, calls = len(OPEN), []

    def space():
        nonlocal position
        position = _SPACE.match(text, position).end()

    while True:
        space()
        if text.startswith(CLOSE, position):
            position += len(CLOSE)
            if position != len(text) or not calls:
                raise ValueError('Expected one nonempty DSML batch')
            return calls
        invoke = _INVOKE.match(text, position)
        if invoke is None or len(calls) >= 256:
            raise ValueError('Invalid DSML invocation')
        name, position = invoke.group(1), invoke.end()
        arguments = {}
        while True:
            space()
            if text.startswith(INVOKE_END, position):
                position += len(INVOKE_END)
                break
            parameter = _PARAMETER.match(text, position)
            if parameter is None or len(arguments) >= 256:
                raise ValueError('Invalid DSML parameter')
            key, string = parameter.group(1, 2)
            if key in arguments:
                raise ValueError('Duplicate DSML parameter')
            end = text.find(PARAMETER_END, parameter.end())
            if end < 0:
                raise ValueError('Incomplete DSML parameter')
            raw = text[parameter.end():end]
            if '<｜｜DSML｜｜' in raw or '</｜｜DSML｜｜' in raw:
                raise ValueError('Nested or ambiguous DSML framing')
            arguments[key] = raw if string == 'true' else load_json(raw)
            position = end + len(PARAMETER_END)
        calls.append({'name': name, 'arguments': arguments})
