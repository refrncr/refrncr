# Standalone hierarchy tables (REF-179)

Product scope and acceptance criteria remain in
[REF-179](https://cleverhorizons.atlassian.net/browse/REF-179).
The persistence decision is [ADR-022](../architecture/adr-022-standalone-hierarchies.md).

## Workflow

Create a Dataset with type **Hierarchy** and an owning team. Creation starts its Concept
through the existing Dataset lifecycle. Add a root, then its children; asymmetric branches
and different leaf depths need no placeholders. Rename or move an existing node to
reorganize its entire subtree while preserving identities and values.

Hierarchy uses the Network icon in navigation and as its default Dataset icon. A chosen
custom icon takes precedence; clearing it restores the Network default.

Custom Fields and a composite Record identifier are optional. Each hierarchy Field has
scope **Leaves only** or **All nodes**. Configure these in Fields after or during structure
maintenance, then enter applicable values on the selected node. Identifier membership can
be configured after nodes exist. Field-level Required and Unique constraints follow scope;
identifier completeness and composite uniqueness apply to leaf Records only.

| View | Content | Editing |
| --- | --- | --- |
| Hierarchy | Names and parent/child structure | Structure and applicable node values through side panels |
| Records | One row per leaf: ancestor names in Level columns, then Leaf, then the leaf's own custom Field values | Leaf values through the Record side panel; details include the full path |
| Hybrid | Complete tree with compact applicable values | Read-only; empty and not-applicable values are distinct |

Switching views keeps the same selected Concept or publication. Root and branch nodes do
not add Records. Values belong to their node; the application does not inherit, copy or
aggregate parent values.

Records presents paths as a flat table. **Level 1** contains the root, **Level 2** the next
ancestor, and further Level columns follow the deepest leaf path in the selected Concept
or publication. Every leaf name occupies the same **Leaf** column. Shorter paths leave
unused ancestor columns empty; this does not create placeholder nodes or additional
Records. Custom Field columns follow Leaf in their configured order and show only that
leaf's own values. Ancestor columns provide name and path context, not inherited Field
values. The view derives levels from the actual ancestor nodes, so a slash within a node
name remains part of that name.

In an editable Concept, each Record also has a pencil action at the right edge of the
table. This column stays visible while scrolling horizontally and opens the existing
Record value panel.

Hierarchy uses icon actions for Edit node, Values and Add child. On desktop they appear
when hovering or focusing a row; touch devices show them continuously. Tooltips retain
the action names and can be dismissed with Escape. Accessible labels include the node
name, and the actions open the same structure and value panels.

## Retention and validation

A leaf that gains children retains its Leaves-only values. Those values become inactive,
are omitted from applicable-value editing and validation, and reactivate if the node becomes
a leaf again. Changing a Field from All nodes to Leaves only similarly retains branch
values; switching back restores them. Both active and inactive values survive publication
and initialization of later Concepts.

Ordinary value edits reject missing required values, invalid datatypes and active uniqueness
conflicts. A structure or scope change that restores conflicting values is saved in the
Concept. Validation identifies the affected node/Field or leaf identifier; publication
remains blocked until the conflict is corrected. Inactive values do not consume uniqueness
and do not require filling currently inapplicable Fields. A Field with retained values
cannot be deleted or retyped without first resolving its stored content.

A publishable hierarchy has one connected root with at least one child, no cycles or
missing parents, unique sibling names and valid applicable constraints. A root-only
Concept remains editable. A valid tree can publish with no custom Fields or identifier.
Published structure, scopes and values are immutable and share the Dataset version's
effective date. Reorganizations require another Concept and publication.

## Implementation boundaries

- `hierarchy.py` owns structure, applicability, constraints and authorized node mutations.
- `hierarchy_contracts.py` exposes immutable node content, with stable UUIDs independent
  of business identifier values.
- `hierarchy_persistence.py` stores nodes and all canonical values. Callers own the
  authorized Dataset transaction and lock; adapter methods do not commit independently.
- `dataset_versions.py`, `dataset_diff.py` and the snapshot adapters integrate hierarchy
  content into the existing lifecycle. Structure comparisons include node names and
  parents, so a rename or move is reviewable even when leaf values do not change.
- Mutable hierarchy content creates no Dataset Record rows. Publication stores the full
  hierarchy and an applicable leaf projection in existing Record snapshot tables. Counts
  and Records views exclude the root and branches.

Dataset authority, active-Concept checks, CSRF, scoped node lookup and expected Dataset
revision remain server-side controls. Hierarchy writers lock the Dataset and commit one
bounded audit event with the mutation; audit metadata does not contain node values.

## Schema upgrade and rollback

Use the normal repository database upgrade process. Migration `0028_ref_179` follows
`0027_ref_177`, adds hierarchy Field scope and four relational node/value tables, and
extends the Dataset kind constraint. Existing Reference and Mapping rows are unchanged.
Composite foreign keys enforce Dataset/version ownership. The SQLite migration runner
continues to own its transaction and verifies foreign keys before completion.

The migration can downgrade an installation with no hierarchy content. It refuses even
an empty hierarchy Dataset, as well as node snapshots or non-null hierarchy Field scopes,
because the previous application cannot preserve those semantics. For an installation
that has adopted hierarchies, restore a pre-upgrade backup with the matching application
version when rollback is needed.

## Verification and follow-up scope

Focused tests cover domain validation, retention/restoration, optional identifiers,
transaction rollback, concurrent edits, complete version snapshots, review and browser
views. Relational constraints and migration round trips run through the shared SQLite and
PostgreSQL fixtures. Set `REFRNCR_TEST_POSTGRES_URL` to an isolated PostgreSQL test database
for local integration runs; GitHub Actions remains the independent pre-merge quality gate.

Run the repository's configured checks using its virtual environment:

```shell
python -m pre_commit run --all-files
python -m pytest
python -m ruff check .
```

Hierarchy export, public API reads, import/reimport and references to other Datasets belong
to REF-181 through REF-184. This phase does not implement those contracts or several named
hierarchies within a Dataset.
