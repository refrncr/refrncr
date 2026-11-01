import csv
import io
from datetime import UTC, date, datetime, time

from refrncr.dataset_export import generate_history_export
from refrncr.ports import RecordValue, StoredRecord
from refrncr.record_intervals import RecordInterval, derive_intervals
from refrncr.version_contracts import PublishedVersion, SnapshotField, SummaryCounts, VersionState

FIELDS = (
    SnapshotField("code", "dataset", "code", "Code", "string", True, True, 1, True),
    SnapshotField(
        "department", "dataset", "department", "Department", "string", False, False, 2, False
    ),
)


def version(number, effective, end=None):
    published = PublishedVersion(
        str(number),
        "dataset",
        number,
        datetime(2026, 1, number, tzinfo=UTC),
        "actor",
        effective,
        "",
        "Cost centres",
        "",
        2,
        2,
        SummaryCounts(),
    )
    return VersionState(published, "Current", end)


def record(identity, department):
    values = [RecordValue("code", "code", "string", identity, b"s" + identity.encode(), True)]
    if department is not None:
        values.append(
            RecordValue(
                "department",
                "department",
                "string",
                department,
                b"s" + department.encode(),
                False,
            )
        )
    return StoredRecord(identity, "dataset", tuple(values), 1)


def test_cc_1001_merges_unchanged_and_returns_real_unclipped_periods():
    first = version(1, date(2026, 1, 1), date(2026, 7, 1))
    second = version(2, date(2026, 7, 1))
    result = derive_intervals(
        (
            (first, FIELDS, (record("CC-1001", "Sales"), record("CC-1002", "Finance"))),
            (second, FIELDS, (record("CC-1001", "Marketing"), record("CC-1002", "Finance"))),
        ),
        lower=date(2026, 4, 1),
        upper=date(2026, 10, 1),
        limit=100,
    )

    assert [
        (row.values["code"], row.values["department"], row.valid_from, row.valid_to, row.version)
        for row in result.rows
    ] == [
        ("CC-1001", "Sales", date(2026, 1, 1), date(2026, 7, 1), 1),
        ("CC-1001", "Marketing", date(2026, 7, 1), None, 2),
        ("CC-1002", "Finance", date(2026, 1, 1), None, 1),
    ]


def test_removed_and_reintroduced_record_has_a_gap_without_deletion_row():
    snapshots = (
        (version(1, date(2026, 1, 1)), FIELDS, (record("CC-1001", "Sales"),)),
        (version(2, date(2026, 3, 1)), FIELDS, ()),
        (version(3, date(2026, 5, 1)), FIELDS, (record("CC-1001", "Sales"),)),
    )
    rows = derive_intervals(snapshots, limit=100).rows
    assert [(row.valid_from, row.valid_to) for row in rows] == [
        (date(2026, 1, 1), date(2026, 3, 1)),
        (date(2026, 5, 1), None),
    ]


def test_added_or_removed_empty_field_does_not_split_visible_state():
    code_only = (FIELDS[0],)
    snapshots = (
        (version(1, date(2026, 1, 1)), code_only, (record("CC-1001", None),)),
        (version(2, date(2026, 2, 1)), FIELDS, (record("CC-1001", None),)),
        (version(3, date(2026, 3, 1)), code_only, (record("CC-1001", None),)),
    )
    result = derive_intervals(snapshots, limit=100)
    assert len(result.rows) == 1
    assert result.rows[0].values == {"code": "CC-1001", "department": None}
    assert result.rows[0].version == 1


def test_added_or_removed_populated_field_splits_visible_state():
    code_only = (FIELDS[0],)
    added = derive_intervals(
        (
            (version(1, date(2026, 1, 1)), code_only, (record("CC-1001", None),)),
            (version(2, date(2026, 2, 1)), FIELDS, (record("CC-1001", "Anna"),)),
        ),
        limit=100,
    )
    assert [(row.values["department"], row.valid_to) for row in added.rows] == [
        (None, date(2026, 2, 1)),
        ("Anna", None),
    ]

    removed = derive_intervals(
        (
            (version(1, date(2026, 1, 1)), FIELDS, (record("CC-1001", "South"),)),
            (version(2, date(2026, 2, 1)), code_only, (record("CC-1001", None),)),
        ),
        limit=100,
    )
    assert [(row.values["department"], row.valid_to) for row in removed.rows] == [
        ("South", date(2026, 2, 1)),
        (None, None),
    ]


def test_temporal_values_use_the_same_canonical_strings_as_the_api():
    fields = (
        FIELDS[0],
        SnapshotField("day", "dataset", "day", "Day", "date", False, False, 2, False),
        SnapshotField("at", "dataset", "at", "At", "time", False, False, 3, False),
    )
    temporal = StoredRecord(
        "CC-1001",
        "dataset",
        (
            RecordValue("code", "code", "string", "CC-1001", b"sCC-1001", True),
            RecordValue("day", "day", "date", date(2026, 3, 4), b"d2026-03-04", False),
            RecordValue("at", "at", "time", time(9, 5, 6), b"t09:05:06", False),
        ),
        1,
    )
    row = derive_intervals(((version(1, date(2026, 1, 1)), fields, (temporal,)),), limit=100).rows[
        0
    ]
    assert row.values == {"code": "CC-1001", "day": "2026-03-04", "at": "09:05:06"}


def test_pagination_applies_after_identifier_and_period_sorting():
    snapshot = (
        version(1, date(2026, 1, 1)),
        FIELDS,
        (record("CC-1002", "Finance"), record("CC-1001", "Sales")),
    )
    result = derive_intervals((snapshot,), offset=1, limit=1)
    assert result.total == 2
    assert [row.values["code"] for row in result.rows] == ["CC-1002"]


def test_export_renames_its_metadata_instead_of_a_conflicting_customer_field():
    row = RecordInterval(
        "identifier",
        {"valid_from": "customer value"},
        date(2026, 1, 1),
        None,
        1,
        (),
    )
    artifact = generate_history_export("csv", ("valid_from",), (row,), "conflict")
    content = artifact.content.read()[3:].decode()
    assert list(csv.reader(io.StringIO(content))) == [
        ["valid_from", "valid_from (period)", "valid_to", "version"],
        ["customer value", "2026-01-01", "", "1"],
    ]
