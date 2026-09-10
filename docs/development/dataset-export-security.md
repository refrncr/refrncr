# Dataset export security and resource behavior

Dataset export is an authorized data-extraction operation with a durable prepared-request audit event. The service resolves the Dataset by
its validated stable key and requires the central `can_export_dataset` capability. Current owning-team
members and active Community administrators qualify automatically; other active users require direct
or current-team export access. Read access and historical Dataset Grants confer no export rights.
Creator metadata is not consulted. Missing, malformed and inaccessible Dataset keys all use the existing
non-disclosing not-found result. The only client-controlled export input is one allowlisted format
value (`csv`, `xlsx` or `json`).

All output is generated before download headers are returned. A `SpooledTemporaryFile` keeps at
most 1 MiB in memory and rolls larger output to an automatically removed temporary file. CSV and
JSON are written incrementally and Excel uses openpyxl write-only mode. Records are read with one
joined, server-side streaming query, sorted in bounded chunks of 1,000 records, spooled to
temporary merge runs, and merged in the existing REF-79/80 order. Multi-pass consolidation keeps
at most 64 merge runs open at once. Every run and output spool is
closed on success or failure. This prevents a late serializer or workbook error from looking like
a successful truncated download. Inside the service snapshot, the completed spool is read once in
64 KiB blocks and rewound; a read failure therefore occurs before HTTP success is constructed.

The adapter starts the snapshot before reading the Dataset. PostgreSQL uses a repeatable-read
transaction; SQLite explicitly begins a read transaction because the sqlite3 legacy mode does not start one for SELECT. Fields, the identifier, Records and values
are read and the artifact is generated before that Unit of Work exits, so one file cannot combine
schema and data from different committed states. Artifact generation takes no write lock. After preparation and readability preflight, the service
closes that snapshot and commits `dataset.export_requested` in a separate audit-only Unit of Work
before returning the artifact. Audit failure closes the spool and produces no download. A later
stream error keeps the accurate request event and uses fixed technical logging, without a second
domain event. See [audit policy](audit-policy.md) and
[ADR-012](../architecture/adr-012-audit-export-boundary.md). A revoke committed after the snapshot begins affects subsequent exports; an already authorized snapshot may finish.

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

REF-145 extends canonical export to Integer, Float, Time, DateTime and Timestamp.
CSV/JSON use the [central datatype forms](field-datatypes.md), including UTC `Z`
for Timestamp. JSON Integer and Float are native numbers. XLSX uses canonical text
for these new types to retain exact digits, fractions and explicit UTC; original
four-type cell behavior is retained. Configurable output formats and additional
typed Excel rendering belong to REF-152, not this datatype foundation.

Typed value and pre-format numeric bounds come from the datatype catalog. String
quoting/formula defenses remain serializer responsibilities. An invalid value
aborts the prepared export without an audit event; XLSX cleanup also closes its
write-only XML generator and removes its temporary file on this path.

## Dataset deletion (REF-141)

An export whose authorized snapshot starts after deletion cannot resolve the Dataset and
fails with the established non-disclosing result without an export event. A snapshot already
in progress or successfully prepared artifact retains ADR-012 semantics, even if deletion
commits before the export audit writer. Its contents remain coherent and its technical cleanup
closes all spools normally. There is no persisted export-status row to cascade. Deletion does
not recall files already prepared or downloaded.
