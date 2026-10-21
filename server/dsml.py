"""Strict, inert decoder for a complete native DeepSeek tool envelope.

Not XML: no entity expansion, external resources, escaping, or tag recovery.
This decoder returns data only; the shared tool parser owns schema validation.
"""
import re

_TOKENS = ('｜DSML｜', '｜｜DSML｜｜')
OPEN = '<｜DSML｜ calls>'
OPENINGS = tuple('<' + token + ' calls>' for token in _TOKENS)
_NAME = r'([^"<>\x00-\x1f]{1,256})'
_SPACE = re.compile(r'[ \t\r\n]*')
_NATIVE_PREFIX = re.compile(r'</?[｜|]+DSML[｜|]+')
_FRAME_LINE = re.compile(r'^ {0,3}</?[｜|]+DSML[｜|]+(?:[ \t]*(?:calls|tool_calls|function_calls|invoke|parameter)\b|>)')


def has_markup(text, *, include_code=False):
    """Recognize native-looking action framing, never authorize or repair it."""
    fence, width = None, 0
    for line in (text or '').splitlines():
        if fence is not None:
            if re.fullmatch(r' {0,3}' + re.escape(fence) + r'{' + str(width) + r',}[ \t]*', line):
                fence = None
                continue
            if not include_code:
                continue
        else:
            opened = re.fullmatch(r' {0,3}(`{3,}|~{3,})([^\r\n]*)', line)
            if opened:
                marker, info = opened.groups()
                if marker[0] != '`' or '`' not in info:
                    fence, width = marker[0], len(marker)
                    continue
        # An already broken explicit tool packet may not hide native framing
        # by indenting it; ordinary quoted/fenced examples remain inert.
        if _FRAME_LINE.match(line.lstrip(' \t') if include_code else line):
            return True
    return False


def envelope(text, *, allow_preamble=False, allow_duplicate_open=False):
    """Locate one explicit native action envelope, never quoted/example code.

    Plain commentary is eligible only when the caller explicitly requested the
    native dialect. This selects a suffix, not a decoder tolerance: the entire
    suffix still has to pass decode() and every advertised argument schema.
    """
    stripped = (text or '').strip()
    opening = next((value for value in OPENINGS if stripped.startswith(value)), None)
    if opening is not None:
        first_line = next((line for line in text.splitlines() if line.strip()), '')
        # Markdown indentation is data, even for otherwise valid native tags.
        return stripped if first_line.startswith(opening) else None
    if not allow_preamble:
        return None
    starts = list(re.finditer(r'(?m)^(?:' + '|'.join(map(re.escape, OPENINGS)) + r')[ \t]*\r?$', text or ''))
    duplicate = (allow_duplicate_open and len(starts) == 2
                 and starts[0].group().strip() == starts[1].group().strip()
                 and text[starts[0].start():starts[1].start()].strip() == starts[0].group().strip())
    if len(starts) != 1 and not duplicate:
        return None
    prefix = text[:starts[0].start()]
    if (any(marker in prefix for marker in ('`', '~~~', '<', '\t'))
            or re.search(r'(?m)^(?:[ \t]*>| {4})', prefix)
            or prefix.rstrip().endswith(':')):
        return None
    return text[starts[0].start():].rstrip()


def decode(text, load_json, *, allow_wrapper_tolerances=False):
    """Decode one whole batch without changing or inferring argument values."""
    token = next((value for value in _TOKENS if text.startswith('<' + value + ' calls>')), None)
    if len(text.encode()) > 4 * 1024 * 1024 or token is None:
        raise ValueError('Invalid DSML envelope')
    opening, closing = '<' + token + ' calls>', '</' + token + ' calls>'
    invoke_end, parameter_end = '</' + token + ' invoke>', '</' + token + ' parameter>'
    generic_end = '</' + token + '>'
    invoke_start = re.compile(r'<' + re.escape(token) + r' invoke name="' + _NAME + r'">')
    parameter_start = re.compile(r'<' + re.escape(token) + r' parameter name="' + _NAME + r'" string="(true|false)">')
    position, calls = len(opening), []

    def space():
        nonlocal position
        position = _SPACE.match(text, position).end()

    if allow_wrapper_tolerances:
        space()
        # One adjacent, empty repeated header contains no action or value.
        # A third header, or one after an invocation, still fails the grammar.
        if text.startswith(opening, position):
            position += len(opening)

    while True:
        space()
        if text.startswith(closing, position):
            position += len(closing)
            if position != len(text) or not calls:
                raise ValueError('Expected one nonempty DSML batch')
            return calls
        invoke = invoke_start.match(text, position)
        if invoke is None or len(calls) >= 256:
            raise ValueError('Invalid DSML invocation')
        name, position = invoke.group(1), invoke.end()
        arguments = {}
        while True:
            space()
            if text.startswith(invoke_end, position):
                position += len(invoke_end)
                break
            if allow_wrapper_tolerances and text.startswith(generic_end, position):
                # At this boundary every parameter is already fully closed.
                # The only open inner element is invoke. Never use this alias
                # to close a parameter, infer a value or close the calls root.
                position += len(generic_end)
                break
            parameter = parameter_start.match(text, position)
            if parameter is None or len(arguments) >= 256:
                raise ValueError('Invalid DSML parameter')
            key, string = parameter.group(1, 2)
            if key in arguments:
                raise ValueError('Duplicate DSML parameter')
            end = text.find(parameter_end, parameter.end())
            if end < 0:
                raise ValueError('Incomplete DSML parameter')
            raw = text[parameter.end():end]
            if _NATIVE_PREFIX.search(raw):
                raise ValueError('Nested or ambiguous DSML framing')
            arguments[key] = raw if string == 'true' else load_json(raw)
            position = end + len(parameter_end)
        calls.append({'name': name, 'arguments': arguments})
