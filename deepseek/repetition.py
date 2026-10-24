"""Bounded observation of repeated DSML structure, never a call decoder.

Only a reply starting with an explicit calls envelope is inspected. Parameter
values, prose and quoted examples are not repetition templates. Failure rejects
the response; nothing is repaired, trimmed or made executable here.
"""
import re

_MARKER = r'(?:｜DSML｜|｜｜DSML｜｜)'
_ROOTS = ('<｜DSML｜ calls>', '<｜｜DSML｜｜ calls>')
_TAG = re.compile(r'(?:</?' + _MARKER + r'(?: [^<>]{0,512})?>|</DSML｜ (?:invoke|calls)>)')
_INVOKE = re.compile(r'<' + _MARKER + r' invoke name="[^"<>\x00-\x1f]{1,256}">')
_PARAMETER = re.compile(r'<' + _MARKER + r' parameter name="([^"<>\x00-\x1f]{1,256})" string="(?:true|false)">')
_PARAMETER_END = re.compile(r'</' + _MARKER + r' parameter>')
_INVOKE_END = re.compile(r'(?:</' + _MARKER + r'(?: invoke)?>|</DSML｜ invoke>)')
_ROOT_END = re.compile(r'</(?:' + _MARKER + r'|DSML｜) calls>')
MAX_REDUNDANT_CLOSES = 16
_TAIL = 560  # Longest recognized tag plus a split marker; independent of reply size.


class RepeatedToolFraming(ValueError):
    pass


class ToolRepetitionGuard:
    def __init__(self):
        self.pending = ''
        self.enabled = None
        self.in_invoke = False
        self.in_parameter = False
        self.fields = set()
        self.redundant_closes = 0

    def feed(self, chunk):
        self.pending += chunk
        if self.enabled is None:
            prefix = self.pending.lstrip()
            root = next((r for r in _ROOTS if prefix.startswith(r)), None)
            if root:
                self.enabled = True
                self.pending = prefix[len(root):]
            elif not any(r.startswith(prefix) for r in _ROOTS) or len(self.pending) > _TAIL:
                self.enabled = False
                self.pending = ''
            else:
                return
        if not self.enabled:
            self.pending = ''
            return
        consumed = 0
        for match in _TAG.finditer(self.pending):
            tag = match.group()
            consumed = match.end()
            if self.in_parameter:
                if _PARAMETER_END.fullmatch(tag):
                    self.in_parameter = False
                    self.redundant_closes = 0
                continue
            if _INVOKE.fullmatch(tag) and not self.in_invoke:
                self.in_invoke = True
                self.fields.clear()
                self.redundant_closes = 0
            elif _INVOKE_END.fullmatch(tag):
                self.in_invoke = False
                self.fields.clear()
                self.redundant_closes = 0
            elif _ROOT_END.fullmatch(tag):
                self.enabled = False
                self.pending = ''
                return
            elif self.in_invoke and _PARAMETER_END.fullmatch(tag):
                self.redundant_closes += 1
                if self.redundant_closes > MAX_REDUNDANT_CLOSES:
                    raise RepeatedToolFraming('Repeated DSML structure')
            else:
                parameter = _PARAMETER.fullmatch(tag)
                if self.in_invoke and parameter:
                    key = parameter.group(1)
                    if key in self.fields or len(self.fields) >= 256:
                        raise RepeatedToolFraming('Repeated DSML structure')
                    self.fields.add(key)
                    self.in_parameter = True
                self.redundant_closes = 0
        # Keep only a possible split tag. Long literal values do not accumulate.
        self.pending = self.pending[consumed:][-_TAIL:]
