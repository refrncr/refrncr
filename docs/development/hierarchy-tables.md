# Standalone hierarchy tables (REF-179)

REF-179 defines one standalone hierarchy per Community Dataset, with stable node identities,
optional custom Fields and versioned publication alongside the Dataset.
The persistence decision is [ADR-022](../architecture/adr-022-standalone-hierarchies.md).

## Workflow

Create a Dataset with type **Hierarchy** and an owning team. Creation starts its Concept
through the existing Dataset lifecycle. Add a root, then its children; asymmetric branches
and different leaf depths need no placeholders. Rename or move an existing node to
reorganize its entire subtree while preserving identities and values. Deleting a non-root
node permanently deletes that complete subtree and all active or retained inactive values
after an explicit confirmation that shows the number of affected nodes. The root cannot
be deleted.

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
| Records: Reference table (default) | Every node: Name, direct Parent name, then own applicable Fields; root Parent is empty | Any node's values through the Record side panel |
| Records: Flattened tree | One row per leaf: ancestor names in Level columns, Leaf, then separate values per applicable level | Leaf values through the Record side panel |
| Hybrid | Complete tree with compact applicable values | Read-only; empty and not-applicable values are distinct |

Switching views keeps the same selected Concept or publication. Both Records
representations derive from the existing nodes. Values belong to their node; the
application does not inherit, copy or aggregate parent values.

The reference table shows each node once, including root and intermediate parents, with
only its direct parent's name in Parent. Leaves-only Fields on parent rows display
**Not applicable**, and retained inactive values stay hidden.

The flattened tree presents paths as a flat table. **Level 1** contains the root, **Level 2** the next
ancestor, and further Level columns follow the deepest leaf path in the selected Concept
or publication. Every leaf name occupies the same **Leaf** column. Shorter paths leave
unused ancestor columns empty; this does not create placeholder nodes or additional
Records. Custom Fields follow Leaf in configured order. Each All nodes Field has a
separate value column for every ancestor level and Leaf. Leaves-only Fields have just
the leaf value column. An absent ancestor leaves its value cell blank; **Empty** means
an existing applicable node has no value. Levels derive from actual ancestor nodes, so
a slash within a node name remains part of that name.

In an editable Concept, each Record also has a pencil action at the right edge of the
table. Records cells stay on one line; wide content scrolls horizontally behind the
fixed action column. This column stays visible and opens the existing
Record value panel. Saving, cancelling and validation errors preserve the selected
Records representation. Published versions provide read-only detail panels.

Hierarchy uses icon actions for Edit node, Values, Add child and Delete branch. On desktop they appear
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
missing parents, names unique across the entire Dataset and valid applicable constraints.
Names are trimmed and compared exactly, preserving case-sensitive behavior. Different
Datasets may reuse names. A root-only
Concept remains editable. A valid tree can publish with no custom Fields or identifier.
Published structure, scopes and values are immutable and share the Dataset version's
effective date. Reorganizations require another Concept and publication.

Subtree deletion changes only the active Concept. Previously published versions retain
their complete historical graph and values. Publishing the changed Concept creates a new
version without the deleted subtree.

Historical snapshots published under the former sibling-only name rule remain readable
and can initialize a Concept unchanged. New publication is blocked until any restored
duplicate names are renamed. Separate duplicate groups can be repaired one at a time;
the application does not rewrite or automatically rename historical data.

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
  hierarchy and an applicable leaf projection in existing Record snapshot tables. Stored
  version Record counts keep this internal leaf meaning. Browser overview and deletion
  counts use all nodes; neither Records representation changes snapshot storage.

Dataset authority, active-Concept checks, CSRF, scoped node lookup and expected Dataset
revision remain server-side controls. Hierarchy writers lock the Dataset and commit one
bounded audit event with the mutation; audit metadata does not contain node names or values.
Relational cascades remove descendant nodes and their values inside the same transaction.

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

## Export schemas (REF-181 and REF-202)

Hierarchy exports use the complete selected Concept or publication snapshot. The editable Excel
workbook contains exactly three worksheets: `Data`, `Flattened` and `Hierarchy`. `Data` is the
all-node table with `Name`, direct `Parent`, and configured Field keys. `Flattened` contains
one row per non-root leaf with root-first `Level N` columns, `Leaf`, and separate Field
columns for applicable ancestor levels and the leaf. Excel deliberately omits technical
UUID columns from all three worksheets; users identify nodes through names, parents and
paths. `Hierarchy` is a readable structural projection and is not an independent import
source of truth.

The hierarchy export menu provides one CSV bundle download. The ZIP contains both
`<dataset>-data.csv` and `<dataset>-flattened.csv`; each includes the leaf `Node ID` and
direct `Parent ID`. JSON contains `schema_version`, snapshot provenance,
ordered Field metadata (key, display name, datatype and scope), and a `nodes` list with
stable IDs, parent relationships, names and applicable own values. Field keys that collide
with reserved structural headings receive a deterministic `Field: ` prefix. `Empty` means
an applicable node has no value; `Not applicable` means the Field scope excludes that node;
an absent flattened level remains blank.

Only applicable values are exported. Retained inactive values remain internal and are not
written to visible cells, metadata or JSON/CSV output. The existing canonical datatype
serializers and spreadsheet-safety encoding apply. Export filenames identify the CSV
variant; one selected snapshot supplies both CSV files and all other representations.

The REF-202 reporting table is a separate, node-based CSV or one-sheet Excel projection. It emits
one row for every node in deterministic preorder, base Field columns in configured display order,
and structural `depth`, `is_leaf`, `sort_order` and `path` columns. `level_1` through the deepest
selected level contain root-first names; levels below a shorter path repeat its final node. Each
All nodes Field also receives a per-level column and follows the same fill-down rule. Leaves only
Fields remain base columns and are blank for branches. Reserved structural headings use a
deterministic `field_` prefix when they collide with Field keys.

Reporting CSV retains UTF-8 BOM, RFC 4180 escaping and spreadsheet-formula protection. Reporting
Excel contains one `Reporting` worksheet, an Excel table with filters and a frozen header. Its
filenames contain the Dataset key, `concept` or `v<number>`, and `reporting`. The browser groups
these downloads under **Reporting table** and the editable workbook under **File for editing**;
JSON and the legacy CSV projections are not separate menu actions. Reporting API links are not
part of this behavior.

## Editable workbook import (REF-183)

The browser workflow at `/datasets/<dataset-key>/concept/hierarchy-import` accepts only
the editable `.xlsx` export. The visible `Data` worksheet is authoritative; `Flattened`
and `Hierarchy` are presentation sheets and are ignored. `Node ID` and `Parent ID` are
the stable structural identifiers. The `Parent` column is display-only, so a renamed
parent and a moved child can be imported in the same workbook.

Import first creates a complete difference preview. Additions, edits, moves, unchanged
rows and proposed removals are shown before any Concept mutation. Missing existing rows
are never removed silently: confirmation must explicitly acknowledge proposed removals.
Invalid identities, references, structure, field values or workbook shape fail closed.
Confirmation applies the validated difference atomically to the draft Concept; publication
remains a separate lifecycle action.

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
