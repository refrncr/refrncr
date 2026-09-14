# ADR-017 — Current Dataset metadata

Status: accepted for REF-162, product-owner decision 2026-09-14.

## Context

The product owner approved editing Dataset name and description independently of
versions, alongside the current icon, ownership and export-access settings. The
Dataset overview must offer configuration without selecting a Concept or version.
This changes the metadata portion of ADR-014 and the restoration behavior of ADR-015.
It belongs to Community/core and introduces no Cloud concern.

## Decision

The Dataset row is authoritative for its current name, description and icon.
Authorized maintainers can update these without an active Concept. These updates
remain serialized and audited, update `updated_at`, and leave the content revision,
content freshness and Concept identity unchanged. Existing ownership authorization
and compare-and-swap checks remain in force.

Published snapshots retain the name and description captured at publication;
existing snapshots are never rewritten. Browser Dataset contexts use the current
name and description. Field definitions, identifiers, Records and values continue
to come from the selected Concept or snapshot. Dataset metadata is excluded from
version comparisons and publication eligibility. Metadata alone cannot produce a
new version. Concept creation and discard preserve the current Dataset metadata.

`/datasets/<key>/configure` is independent of version selection. Its Fields tab is
available read-only. Explicit Concept/version configuration retains the Fields
view and existing content-editing rules. Dataset details, Access management and
Danger zone use existing current permissions; unpublished Dataset existence is
still hidden from actors without maintenance authority.

## Consequences

No schema migration, dependency or public API change is needed for this decision.
Historical snapshot metadata remains stored as publication provenance, while UI
headings identify the current Dataset. This deliberately replaces metadata-only
version publication and metadata restoration on discard. Field and Record mutation
concurrency, export permissions, CSRF and deletion confirmation remain unchanged.
Tests cover both persistence engines, snapshot preservation, publication rejection,
metadata/publication serialization, route authorization and read-only Field navigation.

## Alternatives

Keeping metadata versioned would prevent the approved configuration workflow when
no Concept is active. Rewriting snapshots would destroy historical provenance.
Introducing a second metadata store would duplicate the existing Dataset row.
