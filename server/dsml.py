"""Strict, inert decoder for a complete native DeepSeek tool envelope.

Not XML: no entity expansion, external resources or value repair. Explicit
wrapper tolerances admit only complete, schema-bound structural spellings.
This decoder returns data only; the shared tool parser owns schema validation.
"""
import re

_TOKENS = ('｜DSML｜', '｜｜DSML｜｜')
OPEN = '<｜DSML｜ calls>'
OPENINGS = tuple('<' + token + ' calls>' for token in _TOKENS)
_NAME = r'([^"<>\x00-\x1f]{1,256})'
_SPACE = re.compile(r'[ \t\r\n]*')
_MARKER_PREFIX = r'</?(?:[｜|]+DSML[｜|]+|DSML｜)'
_NATIVE_PREFIX = re.compile(_MARKER_PREFIX)
_FRAME_LINE = re.compile(r'^ {0,3}' + _MARKER_PREFIX + r'(?:[ \t]*(?:calls|tool_calls|function_calls|invoke|parameter)\b|>)')

# A closed, directly advertised object schema can distinguish the wire envelope
# from an actual tool field. Applicators/references/dynamic properties cannot:
# leave those inputs unchanged for the ordinary schema validator to decide.
_DIRECT_OBJECT_KEYS = frozenset({
    'type', 'properties', 'required', 'additionalProperties', 'minProperties',
    'maxProperties', '$schema', '$id', '$comment', 'title', 'description',
    'default', 'examples', 'deprecated', 'readOnly', 'writeOnly',
})


def argument_envelopes(calls, tools):
    """Decode one explicit typed input object, never infer/merge/repair fields.

    Called only after a complete DSML batch was decoded. This returns data;
    the shared parser must still validate every resulting call against the
    unchanged advertised schema before returning any executable batch.
    """
    result = []
    for call in calls:
        arguments = call['arguments']
        matches = []
        for item in tools:
            function = item.get('function', item) if isinstance(item, dict) else None
            if isinstance(function, dict) and function.get('name') == call['name']:
                matches.append(function)
        schema = matches[0].get('parameters') if len(matches) == 1 else None
        properties = schema.get('properties') if isinstance(schema, dict) else None
        required = schema.get('required') if isinstance(schema, dict) else None
        eligible = (isinstance(schema, dict) and schema.get('type') == 'object'
                    and schema.keys() <= _DIRECT_OBJECT_KEYS
                    and isinstance(properties, dict) and 'arguments' not in properties
                    and isinstance(required, list) and bool(required)
                    and all(isinstance(key, str) and key in properties for key in required))
        if eligible and 'arguments' in arguments:
            value = arguments['arguments']
            if (set(arguments) != {'arguments'} or not isinstance(value, dict)
                    or not value.keys() <= properties.keys()):
                raise ValueError('Ambiguous or undeclared DSML input envelope')
            call = {**call, 'arguments': value}
        result.append(call)
    return result


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


def _declared_properties(tools, name):
    """Literal fields of one unambiguous direct schema; never infer a name."""
    matches = []
    for item in tools or []:
        function = item.get('function', item) if isinstance(item, dict) else None
        if isinstance(function, dict) and function.get('name') == name:
            matches.append(function)
    schema = matches[0].get('parameters') if len(matches) == 1 else None
    if (not isinstance(schema, dict) or schema.get('type') != 'object'
            or not schema.keys() <= _DIRECT_OBJECT_KEYS
            or not isinstance(schema.get('properties'), dict)):
        return {}
    return schema['properties']


_SCALAR_KEYS = frozenset({
    'type', 'enum', 'const', 'minimum', 'maximum', 'exclusiveMinimum',
    'exclusiveMaximum', 'multipleOf', 'minLength', 'maxLength', 'pattern', 'format',
    'title', 'description', '$comment', 'default', 'examples', 'deprecated',
    'readOnly', 'writeOnly',
})
_ATTRIBUTE = re.compile(r'([A-Za-z_][A-Za-z0-9_]{0,127})="([^"<>\x00-\x1f]*)"')


def _attribute_arguments(text, position, properties, load_json, vocabulary, invoke_end):
    """One complete attribute-only record, typed solely by explicit scalar schemas.

    This dialect has no XML entities or backslash escapes. Strings remain literal;
    nonstrings must already be JSON scalars of the exact declared type. No unions,
    references, inferred types, merging with parameters, or missing field repair.
    The named invocation closer must immediately follow the record.
    """
    opening = re.compile(r'<' + vocabulary + r' (?=[A-Za-z_][A-Za-z0-9_]{0,127}=")').match(text, position)
    if opening is None:
        return None
    position, arguments = opening.end(), {}
    while True:
        attribute = _ATTRIBUTE.match(text, position)
        if attribute is None or len(arguments) >= 256:
            raise ValueError('Invalid DSML attribute record')
        key, raw = attribute.groups()
        schema = properties.get(key)
        if (key in arguments or key in {'calls', 'invoke', 'parameter'}
                or not isinstance(schema, dict) or not schema.keys() <= _SCALAR_KEYS
                or not isinstance(schema.get('type'), str)
                or schema['type'] not in {'string', 'number', 'integer', 'boolean', 'null'}
                or raw.endswith('\\') or _NATIVE_PREFIX.search(raw)):
            raise ValueError('Ambiguous DSML attribute or schema')
        if schema['type'] == 'string':
            value = raw
        else:
            value = load_json(raw)
            kind = schema['type']
            if not ((kind == 'number' and type(value) in (int, float))
                    or (kind == 'integer' and type(value) is int)
                    or (kind == 'boolean' and type(value) is bool)
                    or (kind == 'null' and value is None)):
                raise ValueError('DSML attribute type does not match schema')
        arguments[key] = value
        position = attribute.end()
        after_space = _SPACE.match(text, position).end()
        if text.startswith('>', after_space):
            closed = invoke_end.match(text, _SPACE.match(text, after_space + 1).end())
            if closed is None:
                raise ValueError('Expected named close after DSML attribute record')
            return arguments, closed.end()
        if after_space == position:
            raise ValueError('Expected space between DSML attributes')
        position = after_space


def decode(text, load_json, *, allow_wrapper_tolerances=False, tools=None):
    """Decode one whole batch without changing or inferring argument values."""
    token = next((value for value in _TOKENS if text.startswith('<' + value + ' calls>')), None)
    if len(text.encode()) > 4 * 1024 * 1024 or token is None:
        raise ValueError('Invalid DSML envelope')
    opening = '<' + token + ' calls>'
    # Opening/parameter tags have only two complete, symmetric marker aliases.
    # Match at structural boundaries; never replace bytes inside argument values.
    # No global replacement: marker-looking bytes inside values are never fixed.
    vocabulary = '(?:' + '|'.join(map(re.escape, _TOKENS)) + ')' if allow_wrapper_tolerances else re.escape(token)
    # Two observed complete NAMED outer closers use </DSML｜ ...>. Their
    # element names identify the already-open level without inferring any tag
    # or argument. Admit this exact spelling only where invoke/calls can end.
    # Parameter boundaries, opening tags and unnamed closures stay unchanged.
    outer_close = '(?:' + vocabulary + '|DSML｜)' if allow_wrapper_tolerances else vocabulary
    closing = re.compile(r'</' + outer_close + r' calls>')
    invoke_end = re.compile(r'</' + outer_close + r' invoke>')
    parameter_end = re.compile(r'</' + vocabulary + r' parameter>')
    generic_end = re.compile(r'</' + vocabulary + r'>')
    invoke_start = re.compile(r'<' + vocabulary + r' invoke name="' + _NAME + r'">')
    parameter_start = re.compile(r'<' + vocabulary + r' parameter name="' + _NAME + r'" string="(true|false)">')
    # Observed omission of the literal `parameter name="` prefix. The exact
    # field name, its closing quote, type flag and complete value remain on the
    # wire. Only at an argument boundary and for a literal advertised field;
    # never substitute a required field, infer a name, or scan/repair values.
    short_parameter = re.compile(r'<' + vocabulary + r' ([A-Za-z_][A-Za-z0-9_]{0,127})" string="(true|false)">')
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
        closed = closing.match(text, position)
        if closed:
            position = closed.end()
            if position != len(text) or not calls:
                raise ValueError('Expected one nonempty DSML batch')
            return calls
        invoke = invoke_start.match(text, position)
        if invoke is None or len(calls) >= 256:
            raise ValueError('Invalid DSML invocation')
        name, position = invoke.group(1), invoke.end()
        properties = _declared_properties(tools, name) if allow_wrapper_tolerances else {}
        fields = frozenset(properties)
        arguments = {}
        last_parameter_close = None
        if properties:
            space()
            attributes = _attribute_arguments(text, position, properties, load_json, vocabulary, invoke_end)
            if attributes is not None:
                arguments, position = attributes
                calls.append({'name': name, 'arguments': arguments})
                continue
        while True:
            space()
            closed = invoke_end.match(text, position)
            if closed:
                position = closed.end()
                break
            generic = generic_end.match(text, position)
            if allow_wrapper_tolerances and generic:
                # At this boundary every parameter is already fully closed.
                # The only open inner element is invoke. Never use this alias
                # to close a parameter, infer a value or close the calls root.
                position = generic.end()
                break
            if allow_wrapper_tolerances and last_parameter_close is not None:
                following = position
                duplicate = parameter_end.match(text, following)
                while duplicate:
                    following = _SPACE.match(text, duplicate.end()).end()
                    duplicate = parameter_end.match(text, following)
                invoke_closed = invoke_end.match(text, following)
                if following > position and invoke_closed:
                    # An adjacent terminal run of redundant named closes
                    # follows an already complete parameter. Only a NAMED
                    # invoke close may end this run. parameter_end already
                    # restricts every close to the two supported marker aliases;
                    # aliases have the same structural meaning here as at the
                    # original parameter boundary. Inspect without changing
                    # position until the whole boundary is proven; never drop
                    # text, another parameter, a call or an incomplete value.
                    # The envelope byte limit also bounds this linear scan.
                    position = invoke_closed.end()
                    break
            parameter = parameter_start.match(text, position)
            if parameter is None and fields:
                candidate = short_parameter.match(text, position)
                if (candidate and candidate.group(1) in fields
                        and candidate.group(1) not in {'calls', 'invoke', 'parameter'}):
                    parameter = candidate
            if parameter is None or len(arguments) >= 256:
                raise ValueError('Invalid DSML parameter')
            key, string = parameter.group(1, 2)
            if key in arguments:
                raise ValueError('Duplicate DSML parameter')
            end = parameter_end.search(text, parameter.end())
            if end is None:
                raise ValueError('Incomplete DSML parameter')
            raw = text[parameter.end():end.start()]
            if _NATIVE_PREFIX.search(raw):
                raise ValueError('Nested or ambiguous DSML framing')
            arguments[key] = raw if string == 'true' else load_json(raw)
            last_parameter_close = end.group()
            position = end.end()
        calls.append({'name': name, 'arguments': arguments})
