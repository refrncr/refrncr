"""REF-84 classification, read isolation, access, audit and bounded projections."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from support import NOW, audits, publish, setup, snapshot

from refrncr.dataset_diff import DatasetDiffService
from refrncr.diff_classification import classify_fields, relative_moves
from refrncr.diff_contracts import DiffCriteria
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import ConflictError, NotFoundError, ValidationError
from refrncr.version_contracts import SnapshotField


def prepared(c):
    setup(c)
    version = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.diff = DatasetDiffService(c.service)
    return version


def opened(c):
    return c.diff.open_review(c.a.id, c.d.id)


def test_summary_details_and_publication_agree(context):
    c = context
    prepared(c)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "b", "label": "Different"}
    )
    c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "a"})
    review = opened(c)
    assert review.counts == c.service.review(c.a.id, c.d.id)[3]
    assert (review.counts.records_added, review.counts.records_changed) == (1, 1)
    page = c.diff.review(c.a.id, review.identity, "Records")
    assert [row.status for row in page.items] == ["Added", "Changed"]
    detail = c.diff.record_detail(c.a.id, review.identity, c.record.id)
    assert detail.record.before_identifier == "a" and detail.record.after_identifier == "b"
    assert [(value.before, value.after) for value in detail.values] == [
        ("a", "b"),
        ("Café", "Different"),
    ]


def test_datatype_replacement_is_only_a_field_change(context):
    c = context
    prepared(c)
    c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 1, {"code": "a"})
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Label", "integer", False, False)
    c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 2, {"code": "a", "label": "42"})
    review = opened(c)
    assert review.counts.fields_changed == 1
    assert review.counts.records_changed == 0
    assert review.counts == c.service.review(c.a.id, c.d.id)[3]
    assert not c.diff.review(c.a.id, review.identity, "Records").items
    assert c.service.read(c.a.id, c.d.key, prefer_concept=True).record_states == {
        c.record.id: "Published"
    }
    c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 3, {"code": "b", "label": "43"})
    review = opened(c)
    detail = c.diff.record_detail(c.a.id, review.identity, c.record.id)
    assert review.counts.records_changed == 1
    assert review.counts == c.service.review(c.a.id, c.d.id)[3]
    assert detail.total == detail.record.changed_values == 1
    assert [(value.before, value.after) for value in detail.values] == [("a", "b")]


def test_schema_changes_do_not_change_records(context):
    c = context
    prepared(c)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "new", "New", "integer")
    review = opened(c)
    assert review.counts.fields_added == 1 and review.counts.records_changed == 0
    assert review.counts == c.service.review(c.a.id, c.d.id)[3]
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "new", "New", "number", False, False)
    assert opened(c).counts.records_changed == 0
    c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 1, {"code": "a"})
    c.s.dataset_fields.delete_field(c.a.id, c.d.key, "label")
    review = opened(c)
    assert review.counts.fields_removed == 1 and review.counts.records_changed == 0
    assert not c.diff.review(c.a.id, review.identity, "Records").items
    assert review.counts == c.service.review(c.a.id, c.d.id)[3]


@pytest.mark.parametrize(
    "before,after,moved",
    [
        (("a", "b", "c"), ("a", "x", "b", "c"), set()),
        (("a", "b", "c"), ("a", "c"), set()),
        (("a", "b", "c"), ("b", "c", "a"), {"a"}),
        (("a", "b", "c", "d"), ("c", "d", "a", "b"), {"c", "d"}),
        (("a", "b", "c", "d"), ("x", "d", "a", "c"), {"d"}),
    ],
)
def test_relative_order(before, after, moved):
    assert relative_moves(before, after) == moved


def test_field_properties_and_corrupt_order():
    field = SnapshotField("id", "dataset", "key", "Name", "string", False, False, 1, False)
    change = classify_fields(
        (
            replace(
                field,
                data_type="integer",
                is_required=True,
                is_identifier=True,
                is_unique=True,
                name="New",
            ),
        ),
        (field,),
    )[0]
    assert {p.name for p in change.properties} == {
        "Datatype",
        "Required",
        "Record identifier",
        "Unique",
        "Name",
    }
    assert change.datatype_changed and not change.moved
    with pytest.raises(ValidationError):
        classify_fields((field, replace(field, id="other", key="other")), ())


@pytest.mark.parametrize(
    "kind", ["owner", "admin", "viewer", "editor", "export", "outsider", "inactive", "deleted"]
)
def test_authorization_every_operation(context, kind):
    c = context
    prepared(c)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    review = opened(c)
    actor = c.a if kind == "admin" else c.u
    if kind in {"owner", "inactive", "deleted"}:
        c.s.memberships.create_membership(c.a.id, actor.id, c.owner.id)
    if kind in {"viewer", "editor"}:
        c.s.grants.create_grant(c.a.id, "user", actor.id, "dataset", c.d.id, kind)
    if kind == "export":
        c.s.datasets.grant_export_access(c.a.id, c.d.key, "user", actor.id)
    if kind == "inactive":
        c.s.users.disable_user(c.a.id, actor.id)
    if kind == "deleted":
        c.s.users.delete_user(c.a.id, actor.id)
    if kind in {"owner", "admin"}:
        assert c.diff.open_review(actor.id, c.d.id).identity.actor_id == actor.id
        return
    before = snapshot(c)
    for dataset_id in (c.d.id, str(uuid4())):
        identity = replace(review.identity, dataset_id=dataset_id, actor_id=actor.id)
        calls = [
            lambda dataset_id=dataset_id: c.diff.open_review(actor.id, dataset_id),
            lambda identity=identity: c.diff.field_detail(actor.id, identity, str(uuid4())),
            lambda identity=identity: c.diff.record_detail(actor.id, identity, c.record.id),
        ]
        calls.extend(
            lambda section=section, identity=identity: c.diff.review(
                actor.id, identity, section, DiffCriteria(search="secret", anchor=str(uuid4()))
            )
            for section in ("Summary", "Fields", "Records")
        )
        for call in calls:
            with pytest.raises(NotFoundError):
                call()
    assert snapshot(c) == before


@pytest.mark.parametrize("change", ["concept", "publish", "cancel", "activate", "delete"])
def test_stale_baseline_and_revision(context, change):
    c = context
    prepared(c)
    if change in {"cancel", "activate"}:
        c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Changed", "string", False, False)
        scheduled = publish(c, day=NOW.date() + timedelta(days=1))
        c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    review = opened(c)
    if change == "activate":
        c.service.clock = lambda: NOW + timedelta(days=1)
    elif change == "cancel":
        c.service.cancel(c.a.id, c.d.id, scheduled.id)
    elif change == "delete":
        target = c.s.datasets.get_dataset_for_deletion(c.a.id, c.d.id)
        c.s.datasets.delete_dataset(c.a.id, c.d.id, target.name, target.expected_updated_at)
    else:
        c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Updated", "string", False, False)
        if change == "publish":
            publish(c)
    before = snapshot(c)
    for section in ("Summary", "Fields", "Records"):
        with pytest.raises(NotFoundError if change == "delete" else ConflictError):
            c.diff.review(c.a.id, review.identity, section)
    with pytest.raises(NotFoundError if change == "delete" else ConflictError):
        c.diff.record_detail(c.a.id, review.identity, c.record.id)
    assert snapshot(c) == before


def test_audit_once_no_noise_and_no_change(context):
    c = context
    prepared(c)
    before = len(audits(c))
    with pytest.raises(ConflictError, match="No changes yet"):
        opened(c)
    assert len(audits(c)) == before
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    before = len(audits(c))
    review = opened(c)
    assert review.has_changes
    for section in ("Summary", "Fields", "Records"):
        c.diff.review(c.a.id, review.identity, section)
    assert len(audits(c)) == before + 1
    event = audits(c)[-1]
    assert event.action == "dataset.version_diff_viewed"
    assert event.actor_user_id == c.a.id and event.object_id == c.d.id
    assert set(event.details) == {
        "baseline_version_number",
        "concept_revision",
        "fields_added",
        "fields_changed",
        "fields_removed",
        "records_added",
        "records_changed",
        "records_removed",
    }
    assert all(type(value) is int for value in event.details.values())


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_open_audit_failure_rolls_back(database_context, monkeypatch, failure):
    c = database_context
    prepared(c)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    before = snapshot(c)

    def fail(*args, **kwargs):
        raise OperationalError("private SQL", {}, Exception("secret"))

    monkeypatch.setattr(
        SqlAlchemyUnitOfWork if failure == "audit" else Session,
        "add_audit" if failure == "audit" else "commit",
        fail,
    )
    with pytest.raises(ConflictError) as error:
        opened(c)
    assert "private" not in str(error.value) and snapshot(c) == before


def test_first_publication_and_cancelled_exclusion(context):
    c = context
    setup(c)
    c.diff = DatasetDiffService(c.service)
    first_review = opened(c)
    assert first_review.identity.baseline_id is None
    assert first_review.counts.fields_added == 2 and first_review.counts.records_added == 1
    first = publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Changed", "string", False, False)
    scheduled = publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Preserved draft", "string", False, False
    )
    assert opened(c).identity.baseline_id == scheduled.id
    c.service.cancel(c.a.id, c.d.id, scheduled.id)
    before = snapshot(c)
    with pytest.raises(ConflictError, match="cancelled"):
        opened(c)
    assert snapshot(c) == before
    ui = c.service.concept_context(c.a.id, c.d.key)
    assert ui.stale and ui.concept.base_version_id == scheduled.id != first.id


@pytest.mark.parametrize("size", [49, 50, 51, 121])
def test_search_filters_and_keyset_pages(context, size):
    c = context
    prepared(c)
    source = "code,label\n" + "".join(f"item{i:03},Value\n" for i in range(size))
    state, _, _ = c.s.record_imports.begin(c.a.id, "session", c.d.key, "rows.csv", source.encode())
    c.s.record_imports.preview(c.a.id, "session", c.d.key, state.token, ("code", "label"))
    c.s.record_imports.confirm(c.a.id, "session", c.d.key, state.token)
    review = opened(c)
    identity = review.identity
    seen, anchor = [], ""
    while True:
        page = c.diff.review(c.a.id, identity, "Records", DiffCriteria(anchor=anchor))
        assert len(page.items) <= 50 and page.total == size
        seen.extend(row.id for row in page.items)
        if not page.next:
            break
        anchor = page.next
    assert len(set(seen)) == len(seen) == size
    if size > 50:
        backwards = c.diff.review(
            c.a.id, identity, "Records", DiffCriteria(anchor=page.previous, backwards=True)
        )
        assert backwards.items[-1].id == seen[((size - 1) // 50) * 50 - 1]
    for search in ("Value", "%", "_", "' OR 1=1 --", "does-not-exist"):
        assert not c.diff.review(c.a.id, identity, "Records", DiffCriteria(search=search)).items
    assert c.diff.review(c.a.id, identity, "Records", DiffCriteria(search="ITEM000")).total == 1
    assert not c.diff.review(c.a.id, identity, "Records", DiffCriteria(statuses=("Removed",))).items


def test_bounded_query_count_and_no_object_graph(database_context, monkeypatch):
    from refrncr.persistence import SqlAlchemyDatasetRecordOperations
    from refrncr.version_persistence import SqlAlchemyVersionOperations

    c = database_context
    prepared(c)
    c.s.dataset_records.update_record(c.a.id, c.d.key, c.record.id, 1, {"code": "b"})
    review = opened(c)

    def forbidden(*args, **kwargs):
        pytest.fail("Full Record graph loaded")

    monkeypatch.setattr(SqlAlchemyVersionOperations, "content", forbidden)
    monkeypatch.setattr(SqlAlchemyVersionOperations, "iterate_records", forbidden)
    monkeypatch.setattr(SqlAlchemyDatasetRecordOperations, "iterate_all", forbidden)
    statements = []

    def collect(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(c.engine, "before_cursor_execute", collect)
    try:
        page = c.diff.review(c.a.id, review.identity, "Records")
        detail = c.diff.record_detail(c.a.id, review.identity, page.items[0].id)
        summary = c.diff.review(c.a.id, review.identity)
    finally:
        event.remove(c.engine, "before_cursor_execute", collect)
    query_limit = 38 if c.engine.dialect.name == "postgresql" else 29
    assert len(statements) <= query_limit
    assert len(page.items) == 1 and len(detail.values) == 2 and summary.counts.records_changed == 1


@pytest.mark.parametrize(
    "data_type,before,after",
    [
        ("string", "Cafe\u0301", "Zoë"),
        ("integer", "0002", "1000000000000000000000000001"),
        ("number", "1.2300", "1.230000000000000001"),
        ("float", "1.2e3", "1.3e3"),
        ("boolean", "false", "true"),
        ("date", "2024-02-29", "2028-02-29"),
        ("time", "12:30:00.1", "12:30:00.2"),
        ("datetime", "2026-09-11T12:30:00", "2026-09-11T12:30:00.000001"),
        ("timestamp", "2026-09-11T12:30:00+02:00", "2026-09-11T12:30:01+02:00"),
        ("string", None, "text"),
        ("string", "text", ""),
    ],
)
def test_canonical_changed_values(context, data_type, before, after):
    from refrncr.datatypes import get_datatype

    c = context
    setup(c, populated=False)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "typed", "Typed", data_type)
    record = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "one", "typed": before})
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.diff = DatasetDiffService(c.service)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, record.id, 1, {"code": "one", "typed": after}
    )
    review = opened(c)
    assert review.counts.records_changed == 1
    assert review.counts == c.service.review(c.a.id, c.d.id)[3]
    detail = c.diff.record_detail(c.a.id, review.identity, record.id)
    assert detail.total == len(detail.values) == 1
    datatype = get_datatype(data_type)

    def rendered(raw):
        value = datatype.canonicalize(raw)
        return datatype.text(value[0]) if value else None

    assert (detail.values[0].before, detail.values[0].after) == (rendered(before), rendered(after))


@pytest.mark.parametrize("before,after", [(None, ""), (" Café ", "Cafe\u0301"), ("", " ")])
def test_canonical_no_change(context, before, after):
    c = context
    setup(c, populated=False)
    record = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "one", "label": before})
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.diff = DatasetDiffService(c.service)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, record.id, 1, {"code": "one", "label": after}
    )
    assert not c.service.concept_context(c.a.id, c.d.key).has_changes
    with pytest.raises(ConflictError, match="No changes yet"):
        opened(c)


@pytest.mark.parametrize(
    "data_type,values,expected",
    [
        ("integer", ["10", "2", "-1"], ["-1", "2", "10"]),
        ("number", ["10.2", "2.2", "-1.2"], ["-1.2", "2.2", "10.2"]),
        ("string", ["é", "a", "Z"], ["Z", "a", "é"]),
        ("date", ["2026-01-02", "2025-01-01"], ["2025-01-01", "2026-01-02"]),
    ],
)
def test_typed_identifier_order(context, data_type, values, expected):
    c = context
    setup(c, populated=False)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "code", "Code", data_type, True, True)
    publish(c)
    c.service.create_concept(c.a.id, c.d.id)
    c.diff = DatasetDiffService(c.service)
    for value in values:
        c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": value})
    review = opened(c)
    page = c.diff.review(c.a.id, review.identity, "Records")
    assert [row.after_identifier for row in page.items] == expected


def test_deleted_and_recreated_identifier_is_two_records(context):
    c = context
    prepared(c)
    c.s.dataset_records.delete_record(c.a.id, c.d.key, c.record.id, 1)
    replacement = c.s.dataset_records.create_record(c.a.id, c.d.key, {"code": "a"})
    review = opened(c)
    page = c.diff.review(c.a.id, review.identity, "Records")
    assert [(row.status, row.id) for row in page.items] == [
        ("Added", replacement.id),
        ("Removed", c.record.id),
    ]
    removed = c.diff.record_detail(c.a.id, review.identity, c.record.id)
    assert [(value.before, value.after) for value in removed.values] == [
        ("a", None),
        ("Café", None),
    ]
    assert (
        c.diff.review(c.a.id, review.identity, "Records", DiffCriteria(statuses=("Removed",))).total
        == 1
    )


def test_search_old_and_new_identifier_and_input_bounds(context):
    c = context
    prepared(c)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "new", "label": "Café"}
    )
    review = opened(c)
    for search in ("a", "NEW"):
        page = c.diff.review(c.a.id, review.identity, "Records", DiffCriteria(search=search))
        assert len(page.items) == 1 and page.items[0].id == c.record.id
    for criteria in (
        DiffCriteria(search="x" * 201),
        DiffCriteria(statuses=("Unknown",)),
        DiffCriteria(anchor="x" * 10000),
        DiffCriteria(statuses=()),
        DiffCriteria(statuses=("Added", "Added")),
    ):
        with pytest.raises(ValidationError):
            c.diff.review(c.a.id, review.identity, "Records", criteria)
    with pytest.raises(NotFoundError):
        c.diff.record_detail(c.a.id, review.identity, str(uuid4()))
    with pytest.raises(NotFoundError):
        c.diff.field_detail(c.a.id, review.identity, str(uuid4()))
    with pytest.raises(ConflictError):
        c.diff.review(c.a.id, replace(review.identity, baseline_id=str(uuid4())))


@pytest.mark.parametrize("corruption", ["key", "text", "identifier", "count", "identity"])
def test_corrupt_snapshot_fails_closed(context, corruption):
    from sqlalchemy import update

    from refrncr.models import DatasetVersion, DatasetVersionRecord, DatasetVersionValue

    c = context
    version = prepared(c)
    with c.sessions.begin() as session:
        if corruption == "key":
            session.execute(update(DatasetVersionValue).values(canonical_key=b"s noncanonical "))
        elif corruption == "text":
            session.execute(update(DatasetVersionValue).values(canonical_text="wrong"))
        elif corruption == "identifier":
            session.execute(update(DatasetVersionRecord).values(identifier_key=b"broken"))
        elif corruption == "count":
            session.execute(
                update(DatasetVersion)
                .where(DatasetVersion.id == version.id)
                .values(record_count=20)
            )
        else:
            # No values: replace a valid stable identity with malformed retained identity.
            from sqlalchemy import delete, insert

            session.execute(delete(DatasetVersionValue))
            session.execute(delete(DatasetVersionRecord))
            session.execute(
                insert(DatasetVersionRecord).values(
                    version_id=version.id, id="bad", identifier_key=b"x"
                )
            )
    before = snapshot(c)
    with pytest.raises((ConflictError, ValidationError)):
        opened(c)
    assert snapshot(c) == before


@pytest.mark.slow
def test_wide_record_and_field_pagination(context):
    c = context
    prepared(c)
    for number in range(101):
        c.s.dataset_fields.create_field(
            c.a.id, c.d.key, f"f{number:03}", f"Field {number:03}", "string"
        )
    record = c.s.dataset_records.create_record(
        c.a.id, c.d.key, {"code": "wide", **{f"f{number:03}": "x" * 1000 for number in range(101)}}
    )
    review = opened(c)
    pages = [c.diff.record_detail(c.a.id, review.identity, record.id, page) for page in (1, 2, 3)]
    assert [len(page.values) for page in pages] == [50, 50, 3]
    assert [page.has_next for page in pages] == [True, True, False]
    assert len({value.field_id for page in pages for value in page.values}) == 103
    first = c.diff.review(c.a.id, review.identity, "Fields")
    second = c.diff.review(c.a.id, review.identity, "Fields", DiffCriteria(anchor=first.next))
    third = c.diff.review(c.a.id, review.identity, "Fields", DiffCriteria(anchor=second.next))
    assert [len(page.items) for page in (first, second, third)] == [50, 50, 1]
    assert not third.next


@pytest.mark.slow
def test_large_projection_has_constant_queries_and_bounded_memory(context):
    import tracemalloc

    c = context
    prepared(c)

    def measure():
        review = opened(c)
        statements = []

        def collect(_conn, _cursor, sql, _parameters, _context, _many):
            if sql.lstrip().upper().startswith("SELECT"):
                statements.append(sql)

        event.listen(c.engine, "before_cursor_execute", collect)
        tracemalloc.start()
        try:
            page = c.diff.review(c.a.id, review.identity, "Records")
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
            event.remove(c.engine, "before_cursor_execute", collect)
        return len(statements), peak, page

    for amount in (100, 10000):
        source = "code,label\n" + "".join(f"{amount}-{i:05},Value\n" for i in range(amount))
        state, _, _ = c.s.record_imports.begin(
            c.a.id, "session", c.d.key, "rows.csv", source.encode()
        )
        c.s.record_imports.preview(c.a.id, "session", c.d.key, state.token, ("code", "label"))
        c.s.record_imports.confirm(c.a.id, "session", c.d.key, state.token)
        measured = measure()
        if amount == 100:
            small = measured
        else:
            large = measured
    assert small[0] == large[0]
    assert len(small[2].items) == len(large[2].items) == 50
    assert large[2].total == 10100
    assert large[1] < 3_000_000
    print(
        "REF84 measured",
        c.engine.dialect.name,
        "SELECTs",
        large[0],
        "peak bytes",
        large[1],
        "records",
        large[2].total,
    )


def test_stale_open_and_publish_have_no_success_event(context):
    c = context
    prepared(c)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    review = opened(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Later", "string", False, False)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        c.diff.open_review(c.a.id, c.d.id, review.identity)
    # Even a client-supplied new revision cannot refresh an old review's baseline.
    with pytest.raises(ConflictError):
        c.service.publish(
            c.a.id,
            c.d.id,
            NOW.date(),
            "",
            review.identity.revision + 1,
            expected_review=review.identity,
        )
    assert snapshot(c) == before


def test_scheduled_activation_blocks_review_publication(database_context):
    c = database_context
    prepared(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Scheduled", "string", False, False)
    publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Next", "string", False, False)
    review = opened(c)
    c.service.clock = lambda: NOW + timedelta(days=1)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        c.service.publish(
            c.a.id,
            c.d.id,
            (NOW + timedelta(days=1)).date(),
            "",
            review.identity.revision,
            expected_review=review.identity,
        )
    assert snapshot(c) == before
    fresh = opened(c)
    published = c.service.publish(
        c.a.id,
        c.d.id,
        (NOW + timedelta(days=2)).date(),
        "",
        fresh.identity.revision,
        expected_review=fresh.identity,
    )
    assert published.number == 3


@pytest.mark.parametrize("kind", ["canonical", "identifier"])
def test_corrupt_concept_open_fails_closed(context, kind):
    from sqlalchemy import update

    from refrncr.models import DatasetRecord, DatasetRecordValue

    c = context
    prepared(c)
    with c.sessions.begin() as session:
        if kind == "canonical":
            session.execute(update(DatasetRecordValue).values(canonical_value_key=b"bad"))
        else:
            session.execute(update(DatasetRecord).values(identifier_key=b"bad"))
    before = snapshot(c)
    with pytest.raises((ValidationError, ConflictError)):
        opened(c)
    assert snapshot(c) == before


def test_calendar_change_during_open_rolls_back(database_context, monkeypatch):
    from refrncr.diff_persistence import SqlAlchemyDiffOperations

    c = database_context
    prepared(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Future", "string", False, False)
    publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(
        c.a.id, c.d.key, "label", "Reviewable draft", "string", False, False
    )
    original = SqlAlchemyDiffOperations.counts

    def cross_boundary(*args):
        result = original(*args)
        c.service.clock = lambda: NOW + timedelta(days=1)
        return result

    monkeypatch.setattr(SqlAlchemyDiffOperations, "counts", cross_boundary)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        opened(c)
    assert snapshot(c) == before


def test_scheduled_activation_during_review_publication_rolls_back(database_context):
    c = database_context
    prepared(c)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Scheduled", "string", False, False)
    publish(c, day=NOW.date() + timedelta(days=1))
    c.service.create_concept(c.a.id, c.d.id)
    c.s.dataset_fields.update_field(c.a.id, c.d.key, "label", "Next", "string", False, False)
    review = opened(c)
    calls = iter([NOW, NOW + timedelta(days=1), NOW + timedelta(days=1)])
    c.service.clock = lambda: next(calls)
    before = snapshot(c)
    with pytest.raises(ConflictError):
        c.service.publish(
            c.a.id,
            c.d.id,
            (NOW + timedelta(days=2)).date(),
            "",
            review.identity.revision,
            expected_review=review.identity,
        )
    assert snapshot(c) == before
