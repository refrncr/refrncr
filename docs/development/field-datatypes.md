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
last, with stable Record-ID tie-breaking. REF-136 general search covers only String,
including String identifiers, using NFC and Unicode casefold. Non-String values
are not searched through their presentation text. Typed Field filters are deferred
at the product owner's request. There is no per-column ordering UI.
See [the search contract and evidence](ref-136-report.md).

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
REF-154 source adapters normalize the source representations documented below
before canonical validation. Native XLSX values retain temporal/numeric type
information; canonical text remains accepted. Import security, staging,
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
These follow-up stories remain outside this change. REF-154 source formats and
import UX are implemented below.

## CSV/XLSX source catalogue (REF-154)

`source_formats.SOURCE_FORMATS` is the immutable, versioned runtime allowlist.
Detection, conversion, UI labels/options and catalogue tests consume it. Source
parsing produces canonical text and then calls the existing datatype contract;
manual entry and output serialization retain their stricter canonical grammars.
There is no locale inference or Field precision/scale configuration.

| Catalogue family | Accepted source representations |
| --- | --- |
| `v1-plain`, `v1-eu`, `v1-us`, `v1-space-comma`, `v1-space-dot` | Integer/Decimal: ungrouped, `1.234,56`, `1,234.56`, `1 234,56`, `1 234.56`; grouping consists of complete groups of three, with a nonzero first group of at most three digits. Integer rejects fractions. |
| `v1-float` | Finite ordinary/scientific numbers, including `102.1059`, `6.7e+07`, `1.25e6` and `6.02E-4`; existing Float digit/exponent, overflow and underflow bounds apply. |
| `v1-boolean` | Case-insensitive true/false, yes/no, y/n, 1/0 and ja/nee. All token pairs may coexist in one column. |
| `v1-date-*` | YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD, DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY, MM-DD-YYYY, MM/DD/YYYY. |
| `v1-time-*` | HH:mm; HH:mm:ss with optional 1–6 fractional digits; h:mm AM/PM; h:mm:ss AM/PM. AM/PM is uppercase and uses a 1–12 hour. |
| `v1-datetime-*` | Every catalogued Date/Time combination separated by a space; YYYY-MM-DD also accepts `T`. These profiles apply to DateTime and explicitly zoned local Timestamp sources. |
| `v1-offset` | The existing explicit-offset/Z Timestamp grammar, normalized to UTC `Z`. |
| `v1-excel-*` | Native Excel Date, Time, DateTime and Timestamp cells; an Excel datetime becomes Date only at midnight, without silently dropping nonzero time. |
| `v1-text` | The existing canonical String normalization. No format selector. |

Excel native temporal and numeric values retain their type during staging.
Native numeric cells use ungrouped numeric interpretation, so an Excel numeric
cell containing 1.234 is not confused with textual thousands grouping. Decimal
uses exact Decimal processing of the value exposed by the XLSX reader; this
cannot restore precision already lost in an Excel floating-point cell.

Real calendar dates, leap years, time ranges and the existing six-digit fraction
limit are enforced. Decimal uses exact base 10 without exponent text, rounding
or accounting negatives. NaN/infinities, malformed exponents and overflow fail.
Only empty CSV values/Excel cells mean null before datatype normalization;
`NULL`, `N/A` and `-` are ordinary String text or invalid typed values. Whitespace
is not treated as an empty numeric row. String retains its existing trim/NFC
contract.

Detection validates the **complete column**, excluding formula cells from
examples/detection (mapped formulas still fail import). It first checks the
last confirmed profile against the complete new column. Otherwise it compares
allowlisted interpretations: equivalent canonical results use catalogue order;
differing results require a choice. Textual `1.234` is never automatically
interpreted as a grouped Integer. Day/month ambiguity and mixed formats cannot
silently select a locale. Choosing a profile applies it to every mapped row.

Local Timestamp profiles require an explicit, bounded IANA timezone. ZoneInfo
round-trips both fold possibilities through UTC. No matching instant is a DST
gap and fails; two distinct instants require **Earlier occurrence** or **Later
occurrence**. That choice applies column-wide. Missing/invalid timezone or fold
decisions return to Map columns. Unix time and month names remain unsupported.
See [Record import security](record-import-security.md) for progressive
disclosure, transaction boundaries and migration/rollback.


### Compact source selection (REF-158)

Temporal choices are a presentation of the unchanged catalogue: eight Date patterns
and four Time families compose all 32 textual DateTime identifiers. The seconds
choice includes optional fractions only where the existing parser allows them.
`format_components` and `compose_format` round-trip each existing identifier;
there is no new persisted identifier or interpretation. Date and Time use only
their own components. Native Excel is a separate source type. Timestamp adds the
existing Z/offset alternative and explicit IANA/fold controls for local sources.

Detected and successfully remembered proposals initialize the same components.
Float's one fixed `v1-float` profile accepts ordinary and scientific notation in
one column; its parser and canonical finite-double representation are unchanged.
Decimal remains exact and rejects exponent notation. See
[the REF-158 report](ref-158-report.md) for validation evidence and
[import security](record-import-security.md) for form validation.

## Descriptive Field metadata (REF-162)

Fields optionally store Definition (at most 4,000 characters) and Unit (at most
64 characters). Both are escaped plain text; empty input stores null and embedded
NUL characters are rejected consistently on SQLite and PostgreSQL. Units are
labels such as EUR, kg, % or days, with no parsing, conversion or calculation
semantics. Neither property affects Record validation or canonical values.

Create/edit and the persistence ports carry both properties. A metadata-only edit
participates in the existing Field revision/audit and review path. Omitted service
update arguments preserve existing metadata; an empty string explicitly clears it.
Configure shows both values; Record column headers expose them in keyboard-accessible
`details` elements rather than hover-only tooltips.
