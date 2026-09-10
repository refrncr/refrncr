# Canonical Field datatypes (REF-145)

The framework-independent `refrncr.datatypes` catalog owns parsing, canonical
text, stable key encoding, typed ordering, identifier eligibility, technical
limits and safe error classes. Field and Record services, import profiling,
persistence decoding and web presentation reuse it. Services preserve their
existing `ValidationError` boundary. No Flask or SQLAlchemy dependency enters
the catalog. See [ADR-013](../architecture/adr-013-canonical-field-datatypes.md).

## Types and canonical forms

| Stable ID | Label | Canonical text | Python value | Identifier eligible |
| --- | --- | --- | --- | --- |
| `string` | String | Existing trimmed NFC Unicode | `str` | Yes |
| `integer` | Integer | Ungrouped base 10, e.g. `-123` | `int` | Yes |
| `number` | Decimal | Exact base 10 with `.`, e.g. `123.45` | `Decimal` | Yes |
| `float` | Float | Shortest round-trip double representation, e.g. `1.25e+30` | `float` | **No** |
| `boolean` | Boolean | `true` or `false` | `bool` | Yes |
| `date` | Date | `YYYY-MM-DD` | `date` | Yes |
| `time` | Time | `HH:mm:ss[.fraction]` | naive `time` | Yes |
| `datetime` | DateTime | `YYYY-MM-DDTHH:mm:ss[.fraction]` | naive `datetime` | Yes |
| `timestamp` | Timestamp | `YYYY-MM-DDTHH:mm:ss[.fraction]Z` | UTC-aware `datetime` | Yes |

There are exactly nine types. Decimal deliberately retains its historical ID
`number`, including in forms and direct service calls. `decimal` is not a tenth
type or accepted alias. Unknown IDs and differently cased IDs fail closed.

A datatype defines meaning and canonical values. A source format describes how
an external value is read; an output format describes how it is presented.
REF-145 adds neither format configuration nor recognition of locale variants.
String has no UUID, email, URL, country, currency, postcode, IBAN or other
semantic validation. Its allowed content is Unicode subject only to the
existing technical normalization, control-character and byte limits.

## Technical limits and normalization

All textual input is limited to **4096 code points before parsing**. String then
strips Unicode whitespace, normalizes NFC and limits the result to **1000 UTF-8
bytes**. Empty normalized String becomes null. Interior Unicode `Cc` controls
and invalid Unicode surrogates are rejected; trailing whitespace retains the
existing trimming behavior, including tabs/newlines at the edges.

`None` and empty text mean null for every type. Null has no stored value row,
does not occupy a uniqueness key and is rejected for required/identifier fields.
For every non-String type, whitespace (including Unicode whitespace), control
characters and trailing content are rejected. Only ASCII numeric grammars are
accepted; there is no locale fallback.

- **Integer:** optional minus and 1–1000 ASCII digits. Leading zeroes are
  accepted and removed; `-000` and `0` both become `0`. Leading plus, decimal
  points, grouping and exponents are rejected. Parsing uses Python integers,
  with no floating-point intermediate or machine-integer truncation.
- **Decimal:** historical grammar `-?(0|[1-9][0-9]*)(\.[0-9]+)?`.
  Maximum 20 integer digits and 18 fractional digits, hence 38 total digits.
  Leading plus, redundant integer zeroes, exponents and grouping are rejected.
  Fractional trailing zeroes are removed from canonical text/keys, and signed
  zero becomes `0`. Parsing and serialization do not round or pass through
  binary Float. There is no per-Field precision/scale setting.
- **Float:** optional minus, ASCII digits, optional decimal fraction, optional
  `e`/`E` exponent with optional sign. At most 1000 mantissa digits; exponent
  has at most three digits and magnitude at most 400. Leading mantissa plus,
  grouping, hex floats, underscores and malformed exponents are rejected.
  Overflow, NaN and infinities fail. Nonzero values that underflow to zero
  fail; representable subnormals are accepted. Both signed zeroes become `0.0`.
  Canonical text is Python's shortest round-trip `repr` of the normalized finite
  IEEE 754 binary64 value. It may contain an exponent and never claims exact
  decimal arithmetic. See [Python's floating-point documentation](https://docs.python.org/3.12/tutorial/floatingpoint.html).
- **Boolean:** only lowercase `true` and `false`; no yes/no, 1/0 or locale tokens.
- **Temporal types:** four-digit Gregorian years 0001–9999, real calendar dates
  and leap years; hours 00–23, minutes/seconds 00–59. Leap seconds and 24:00 are
  rejected. Seconds are mandatory. Fractions contain 1–6 digits and are never
  rounded/truncated; more digits fail, even if zero. Nonzero fractions lose only
  trailing zeroes; all-zero fractions disappear. Uppercase `T` is mandatory
  between date and time. Date accepts no time; Time accepts no date or zone;
  DateTime accepts no zone. Neither civil type consults a browser/server timezone.
- **Timestamp:** requires uppercase `Z` or an explicit `±HH:mm` offset, up to
  23:59 inclusive. Both input and resulting UTC date must be in years 0001–9999.
  Equivalent offset representations normalize to the same instant and key.
  RFC 3339's `-00:00` denotes known UTC with unknown local offset and therefore
  normalizes to `Z`; local-offset metadata is not stored. See
  [RFC 3339 §4.3 and §5.6](https://www.rfc-editor.org/rfc/rfc3339.html#section-4.3).
  No IANA source timezone, DST inference, Unix seconds/milliseconds/microseconds
  or guessed local instant is supported.

Decimal is appropriate when the decimal value itself must be exact. Float is
appropriate for scientific/technical measurements and calculations that accept
binary approximation; it is unsuitable for Record identifiers. Choosing Float
does not add arithmetic or scientific workflows to REFRNCR.

Errors expose fixed bounded messages and one of `unknown_type`, `invalid_value`,
`limit_exceeded`, `invalid_storage`. They never embed the rejected value. Stored
keys must have the correct prefix and re-encode byte-identically after parsing.

Typed Python values are checked before formatting too: Integer magnitude and
Decimal coefficient/exponent/scale are bounded before fixed-point allocation.
Non-finite values and UTC overflow become bounded `DatatypeError` results.
Export uses the catalog's typed validation; its existing String serializer still
quotes/neutralizes arbitrary text defensively, independently of ingress validation.
This does not make noncanonical String input valid at the Record service boundary.

## Equality, uniqueness, ordering and Field changes

Canonical equivalence defines equality and database uniqueness. Original binary
prefixes `s`, `n`, `b`, `d` remain unchanged; Boolean keys remain `b1`/`b0` even
though canonical text is `true`/`false`. New prefixes are `i`, `f`, `t`, `l`, `z`.
Keys are length-prefixed when composed into an identifier, still limited to
2000 bytes in total. Canonical storage keys are not lexicographic sort keys.

Typed Python values determine ascending ordering: numeric magnitude, Gregorian
date/time order, UTC instant order, false before true and UTF-8 byte String order.
The catalog places null last. Record pages and bounded-memory export continue
ordering by identifier field keys; incomplete historical identifiers remain
last, with stable Record-ID tie-breaking. There is currently no Record value
filter endpoint or general per-column ordering UI. Future typed filters must
use the same parsed values rather than casting stored text differently on each
database. No filtering functionality was invented for this story.

Field create/edit exposes all nine labels. A datatype change is allowed only
when **that Field has no non-null stored value**, including when other Records
exist. The existing `field_has_values` port checks this under the Dataset lock.
There is no automatic conversion. Same definitions are true no-ops. Existing
required-field, uniqueness and identifier-membership rules still apply.

Float is rejected in Field creation, direct Field update, combined definition/
membership update, standalone identifier replacement and Record value validation.
The Field form also disables/clears identifier membership for Float, including
dynamically loaded panels. JavaScript is presentation assistance; direct requests
cannot bypass the service. With JavaScript disabled, an existing Float Field can
be saved as an eligible type before configuring identifier membership.

## Storage, upgrade and compatibility

Old field IDs, Record values, canonical/unique/identifier keys and audits are not
rewritten. Original types retain their four typed columns and exactly-one-value
constraint. The exact Decimal key remains authoritative because SQLite's Numeric
projection can differ from PostgreSQL's exact Numeric representation.
This historical SQLite projection may use binary floating point internally; it
is not used to reconstruct Decimal, compare values, form identifiers or render
exports. Those operations use the exact canonical key. Retaining the projection
avoids rewriting old content and does not claim SQLite Numeric itself is exact.

New types store their canonical text in the existing `string_value` column plus
their typed canonical key. The Field type selects parsing; a stored Integer does
not become a String datatype. All adapters return typed values to services.
New temporal values therefore avoid driver timezone/fraction coercion; large
integers avoid SQLite integer bounds. No new table, tenant column or ORM value
model is introduced.

Migration `0014_ref_145` follows `0013_ref_39` and replaces only the Field type
CHECK constraint. Before DDL it checks supported old IDs, canonical keys, typed
column shape, finite numeric projections, unique-key consistency and identifier
definitions. Invalid history produces a bounded error with no Record content.
It never invents repairs or backfills. SQLite's existing explicit migration
transaction/FK restoration and PostgreSQL's transaction own all changes.

Stop application writers and take a restorable backup before the normal Alembic
upgrade (`.venv/bin/python -m alembic upgrade head`, using the installation's
configured database URL). Startup `prepare_database` also invokes the upgrade.
The migration performs its read-only preflight before modifying the constraint;
no additional public preflight CLI is introduced. On failure, inspect/repair the
installation through an authorized operator process, or restore the backup,
then rerun with writers still stopped. Never edit historical migrations.

Downgrade to 0013 is lossless only while all Fields still use the four historical
IDs. It fails before DDL if any new type is configured, even on an empty Field;
there is no silent reinterpretation or deletion. Restore a matching pre-upgrade
backup if reverting a deployment with new type definitions. Historical fixtures
exercise supported old-type downgrades before constructing older baselines.

## Interfaces, authorization and audit

Manual Record services and current CSV/XLSX adapters use the same canonicalizer.
Existing XLSX native Boolean/date conversion is retained; extending native
temporal source interpretation belongs to REF-154. Canonical text cells for the
new types already pass through the existing adapter. Import security, staging,
schema fingerprinting, atomic confirmation and one-summary-event policy remain.

CSV and JSON serialize new types canonically; JSON Integer/Float remain numbers,
and Decimal remains an exact native JSON number without a Float intermediate.
Temporal JSON values are strings; Timestamp is UTC `Z`. XLSX exports new types
as canonical text to preserve exact digits/fractions and explicit UTC. Existing
four-type XLSX behavior and formula protections remain. Configurable typed XLSX
rendering and display formats belong to REF-152.

Both the full-page Record editor and embedded view/edit sidepanels render from
the catalog. Python's default Decimal exponent or datetime space/offset strings
are never used as edit input. Saving unchanged sidepanel values preserves the
Record revision. Failed XLSX preparation closes the write-only XML writer and
removes its temporary file as well as closing the workbook/output spool.

Maintenance remains authorized by owning-team membership or Community admin.
The web supplies actors from authenticated sessions. All business mutations and
their existing minimal structural audit events share one UoW. No Record values,
names or arbitrary supplied metadata enter audit context. Validation, denial,
stale writes, no-ops and rollback emit no success event; audit-insert failure
rolls back business state. Export retains ADR-012's established separate audit
transaction after preparation. No new event contract or actor source is added.

Record revisions, order expectations and ownership expectations are retained;
Field definitions have no general revision contract and none is introduced.
CSRF, escaping, non-disclosing lookup errors and Dataset-scoped Record access
remain enforced. Community core is reusable by Cloud via existing ports without
organisation/tenant keys or hosted-service infrastructure.

## Adding a future datatype

1. Refine its Jira scope, canonical equivalence, limits, identifier eligibility,
   nulls and temporal/numeric meaning before implementation.
2. Add one catalog entry with a unique stable ID/prefix, bounded strict parser,
   deterministic formatter and Python comparison type. Preserve existing entries
   and keys; changing equivalence needs a separate compatibility decision.
3. Extend the database constraint in a new migration, with fail-closed preflight,
   preservation and downgrade tests; never edit an old migration.
4. Check canonical text storage, typed adapter reads, service identifiers,
   no-ops, imports and canonical export on both engines. Review performance and
   driver/resource limits; do not assume database text ordering is typed order.
5. Derive UI labels/eligibility from the catalog, maintain keyboard/no-JavaScript
   behavior, and add unit, service, browser/controller and migration tests.
6. Review authorization, audit atomicity, dependencies/API/documentation impact
   and complete the configured local checks and GitHub CI before merge.

REF-150 owns optional semantic validation; REF-151 owns configurable Timestamp
export timezones; REF-152 owns output formats; REF-153 owns typed JSON import;
REF-154 owns CSV/XLSX source formats and their UX. None is implemented here.
