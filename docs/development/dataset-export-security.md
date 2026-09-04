# Dataset export security and resource behavior

Dataset export is an authorized, read-only browser operation. The service resolves the Dataset by
its validated stable key and requires effective viewer access; editor and Dataset-admin grants
inherit that permission, and the existing active Community-admin override applies. Ownership
metadata is not consulted. Missing, malformed and inaccessible Dataset keys all use the existing
non-disclosing not-found result. The only client-controlled export input is one allowlisted format
value (`csv`, `xlsx` or `json`).

All output is generated before download headers are returned. A `SpooledTemporaryFile` keeps at
most 1 MiB in memory and rolls larger output to an automatically removed temporary file. CSV and
JSON are written incrementally and Excel uses openpyxl write-only mode. Records are read with one
joined, server-side streaming query, sorted in bounded chunks of 1,000 records, spooled to
temporary merge runs, and merged in the existing REF-79/80 order. Multi-pass consolidation keeps
at most 64 merge runs open at once. Every run and output spool is
closed on success or failure. This prevents a late serializer or workbook error from looking like
a successful truncated download. Before response construction, the completed spool is read once in
64 KiB blocks and rewound; a read failure therefore occurs before HTTP success is constructed.

The adapter starts the snapshot before reading the Dataset. PostgreSQL uses a repeatable-read
transaction; SQLite uses its native read transaction. Fields, the identifier, Records and values
are read and the artifact is generated before that Unit of Work exits, so one file cannot combine
schema and data from different committed states. Export takes no write lock, writes no audit event
and commits no data.

CSV uses UTF-8 with BOM, comma delimiters, CRLF records and standard-library RFC 4180 quoting. For
a string whose first non-whitespace character is `=`, `+`, `-` or `@`, export prefixes one ASCII
apostrophe. This is intentionally a visible, deterministic round-trip change: importing the CSV
as text returns the apostrophe too. It applies only to string fields; typed numbers, booleans and
dates are never modified.

Excel uses one visible write-only worksheet. Formula-like strings are forced to the string cell
type, dates use `yyyy-mm-dd`, and no formulas, macros, external links or connections are created.
Decimals with at most 15 significant digits use numeric cells. Larger supported decimals use
literal string cells because Excel numeric cells cannot retain them exactly; preserving the exact
external value takes precedence over an unsafe numeric approximation.
JSON is emitted directly as UTF-8. Decimal tokens come from Python `Decimal` in plain fixed-point
notation, without conversion through binary floating point or exponent notation.
