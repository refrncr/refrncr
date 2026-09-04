# Record import security

Community Record imports stage at most 10 MiB in a bounded, process-local transient store. An
opaque token binds the bytes, mapping, worksheet, actor, login session, and Dataset for 15 minutes.
Logout removes session-bound imports; confirmation consumes the token before reparsing and uses one
serialized Dataset transaction. No staged content or import history is persisted.

CSV parsing uses Python's standard library. Modern `.xlsx` parsing uses `openpyxl` (MIT licensed) in
read-only mode with formulas left unevaluated. A ZIP preflight bounds entry count, expanded size,
and compression ratio and rejects macros, external links, and external data connections. Only
visible worksheets can be selected and mapped formula cells are rejected.

The transient store is deliberately process-local because the supported Community server profile
uses one worker. Multi-worker deployments require sticky routing for the complete import flow;
without it, a later step safely reports the token as expired or unavailable and requires a new
upload. Process-local tokens can never resolve to another process's import, and no writes begin
until confirmation resolves and consumes the token in the receiving process.

## Mapping assistance

Mapping assistance is deterministic and local. Comparison trims whitespace, applies Unicode NFC
normalization and case folding, optionally removes diacritics for comparison only, splits common
camelCase boundaries, and treats underscores, hyphens, and repeated whitespace as equivalent word
separators. Original headers and values remain unchanged for display and validation.

Each source column is profiled once from at most the first 100 parsed source rows. At
most 25 non-empty, non-formula values per column are checked with the same string, decimal, boolean,
and date grammar used by manual Record entry. The mapping page displays at most the first three of
those values, truncating display text at 80 characters. Formula text and cached formula results are
never profiled or displayed.

Candidate ranking keeps name evidence primary: exact immutable keys rank first, followed by exact
normalized keys, exact normalized display names, and limited shared-name-token matches. Type
compatibility can support or reduce a named candidate but cannot create one. Results are ordered by
rank and then immutable field key, limited to three suggestions, and translated to High, Medium, or
Low confidence without exposing numeric scores.

Only one clear, type-compatible High candidate is selected automatically. Near ties remain
unmapped. When source columns compete for one target, only a strictly stronger match may be
selected; ties require a user choice. Required and identifier status affects only the completeness
summary, never candidate ranking. Explicit manual mappings and `Ignored` choices remain authoritative
after server-side identity, cardinality, and Dataset-scope validation.

The assistant uses no external AI, machine learning, telemetry, network service, saved aliases, or
durable mapping templates. Profiling is bounded independently of the 10,000-row import limit and
does not add database queries per source column or candidate. Existing deterministic Dataset field
order is consumed as supplied; REF-121 can later replace that order centrally without stored order
state in the import feature.
