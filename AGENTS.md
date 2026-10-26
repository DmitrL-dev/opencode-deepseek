# Documentation maintenance

For every user-visible change to setup, authentication, commands, provider support,
limits or recovery, update the root README.md and README.en.md in the same change.
Detailed docs alone are not sufficient: the repository front page must describe
the current workflow and link to setup details. Distinguish implemented behavior,
offline verification and live account verification; do not imply tests that were
not performed. Keep both languages consistent.

# Tool-call parsing

Do not add tolerances for malformed provider output. A reply that does not match
the advertised contract (DSML or a fenced `tool_calls` block) stops the step with
`invalid_tool_response`; keep it as evidence instead of teaching the parser a new
spelling. Parser changes are limited to bridge defects: a valid reply that the
bridge rejected or altered, proven by the saved reply. Report live reliability
with `python -m server.trace_report logs`, never with offline test counts alone.
