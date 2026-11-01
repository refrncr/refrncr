# ADR-025: Record validity periods for consumers

Status: proposed for REF-339 (subtasks REF-340, REF-341, REF-342). Product owner decisions
recorded on 2026-11-01. Not implemented.

## Context

ADR-014 stores every publication as a complete immutable snapshot with an effective date.
ADR-019 and the REST API select one snapshot per request through `effective_date=` or
`version=`. Exports (CSV, XLSX, JSON) also write one version. Version metadata carries
`effective_from` but no end, records carry no validity period, and the public API
documentation lists history listing as excluded.

Analysts and data engineers typically join reference data to facts over a reporting period
(the website example: a cost centre moves from Sales to Operations on 1 March). With
snapshots only, every consumer must discover change dates without an API to list versions,
fetch one snapshot per date, derive `valid_from`/`valid_to` itself, merge unchanged records
and infer deletions from absence. Each team would implement this differently, which moves
the "many inconsistent copies" problem from the data into the consumers' logic.

Relevant existing behaviour:

- `version_contracts.py` already computes `valid_until` per version: the effective date of
  the next non-cancelled version.
- Publication requires an effective date of today or later (`dataset_versions.py`), so a
  state that has applied never changes afterwards.
- A dataset has at most one Scheduled version; while it exists no further version can be
  published (`dataset_versions.py`, "Cancel the Scheduled version or wait until it is
  Current").

## Decision

Keep the version model unchanged and add a derived, read-only interval view in three steps.

1. **`effective_to` in version metadata** (REF-340): the existing `valid_until`, exclusive,
   `null` when no later non-cancelled version exists. A published Scheduled version ends the
   current version's period immediately; cancelling it restores the previous value.
2. **Version list endpoint** (REF-341): published, non-cancelled versions of a dataset with
   number, publication time, `effective_from`, `effective_to` and status, ordered by
   effective date, paginated like other collections.
3. **Record intervals** (REF-342): one row per record state with `identifier`, `values`,
   `valid_from`, `valid_to` and `version` (the version in which the state started), through
   the API and through the export dialog described below.

### Period semantics

- Periods are half-open: `valid_from` inclusive, `valid_to` exclusive. Consecutive periods
  of a record line up without gaps or overlap; a gap appears only when a record was removed
  and later re-added under the same business identifier.
- `valid_to` is `null` for an open period in JSON. Export may offer `9999-12-31` instead.
- A request returns every state whose period overlaps the requested period, without
  clipping. Without a period the full history is returned; a single bound is allowed.
  Results are paginated with `offset`/`limit` (maximum 100).
- Only published data counts. Drafts never appear. Cancelled versions never appear and
  never split a period. Scheduled versions appear when the requested period overlaps them,
  consistent with the existing selectors.
- A removed record's period ends at the effective date of the removing version; there is
  no separate deletion row.

### When a period splits

A row splits as soon as the values visible for the record change. A field that does not
exist in a version counts as empty (null). The result contains every field that existed at
some point in the returned periods.

| Change in the next version | Visible values | Split |
|---|---|---|
| Value of an existing field changes | Sales → Operations | yes |
| Field added, record gets a value | null → value | yes |
| Field added, record stays empty | null → null | no |
| Field removed, record had a value | value → null | yes |
| Field removed, record was already empty | null → null | no |
| Nothing changes for the record | unchanged | no |

### Visibility of Scheduled versions

Scheduled versions are visible to API readers with export access, as they already were
through `version=` and a future `effective_date`. `effective_to` therefore shows a Scheduled
successor's effective date, the version list includes Scheduled versions, and the interval
view includes them when the requested period overlaps them.

The browser UI deliberately hides Scheduled successors from users who cannot maintain the
dataset (`_visible_state` in `dataset_versions.py`). This difference is intentional
(product owner, 2026-11-01): API consumers need to see upcoming changes in time, and
content that must stay confidential remains in a draft until it may be published.

### Export dialog

The **Reporting table** group of the export menu offers one item, *Reporting table…*, that
opens a dialog:

- *This version* (default) produces the existing snapshot export of the version or draft
  being viewed.
- *Period* produces the interval rows across all published versions, without draft
  changes, for an optional From/To range (empty means the whole history). The open end is
  written as empty (default) or `9999-12-31`.
- File format: Excel or CSV. History in JSON is available through the API. Hierarchies have
  no *Period* option.

### Consumer contract

Consumers join on date with the standard slowly changing dimension (type 2) pattern:

```sql
join cost_centres k
  on f.cost_centre = k.code
 and f.booking_date >= k.valid_from
 and (f.booking_date < k.valid_to or k.valid_to is null)
```

## Consequences

- Consumers no longer need to know version numbers or change dates, and every consumer
  derives the same periods.
- Version snapshots remain the single source of truth; intervals are computed from them and
  need no new mutable state. Whether they are computed at read time or materialized is an
  implementation choice for REF-342.
- Removing a populated field creates an extra row for every record that had a value. This
  is accepted because field removal is rare and correctness on any date takes precedence.
- Authorization, ETag and caching follow ADR-019; ETags must change when `effective_to`
  changes through publication or cancellation of a Scheduled version.
- Documentation: the website docs (`refrncr-web`, section *Drafts and versions*, commit
  `2a809c0`) describe the current snapshot behaviour only. A comment on REF-339 requires
  them to be updated in the release that ships the interval view, in English and Dutch.

## Alternatives considered

- **Consumers stitch snapshots themselves.** Rejected: duplicated, error-prone logic in
  every consumer, and no API to discover change dates.
- **One row per version per record.** Rejected: unchanged records repeat for every
  version and consumers must still merge them.
- **A removed field never splits a period.** Chosen first on 2026-11-01 and replaced the
  same day: rows would keep a removed field's old value after the field ceased to exist,
  and added and removed fields would need different rules.
- **Any schema change splits every record.** Rejected: adding or removing an empty field
  would create a row per record without any visible change.
- **Clip periods to the requested range.** Rejected: hides when a state really started or
  ended; consumers can clip themselves.
- **Mandatory period on history requests.** Rejected: warehouse loads need the full
  history; pagination bounds response size.
- **A separate History export item.** Rejected: users choose between one moment and a
  period in a single *Reporting table…* dialog, and do not need to know that history is a
  different export.
- **Hide Scheduled successors from API readers who cannot maintain the dataset**, matching
  the browser UI. Rejected on 2026-11-01: it would close existing API access to Scheduled
  versions and complicate the version list and interval view, for a confidentiality need
  that rarely applies to reference data.
- **Publication-time (`known_at`) queries.** Deferred: unnecessary while effective dates
  cannot be in the past. Revisit if retroactive publication is ever allowed.
