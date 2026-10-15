import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from dataset_helpers import create_owned_dataset, downgrade_historical_fixture, publish_dataset
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from support import PASSWORD

from refrncr.app import create_community_services
from refrncr.db.base import Base
from refrncr.db.session import create_database_engine, create_session_factory
from refrncr.models import AuditEvent, DatasetRecord, DatasetRecordValue, utc_now
from refrncr.persistence import SqlAlchemyUnitOfWork
from refrncr.services import (
    NotFoundError,
    RecordConflictError,
    RecordValidationError,
    ResourceTypeRegistry,
    StaleRecordError,
    ValidationError,
    canonicalize_record_value,
    parse_expected_revision,
)
from refrncr.web import create_flask_app


def configured_dataset(services, admin, key="records"):
    dataset = create_owned_dataset(services.datasets, admin.id, key, "Records", "")
    fields = {}
    for field_key, data_type, required, unique in (
        ("code", "string", True, False),
        ("amount", "number", True, False),
        ("active", "boolean", False, False),
        ("day", "date", False, True),
    ):
        fields[field_key] = services.dataset_fields.create_field(
            admin.id,
            key,
            field_key,
            field_key.title(),
            data_type,
            is_required=required,
            is_unique=unique,
        )
    services.dataset_business_keys.set_business_key(admin.id, key, ("code", "amount"))
    return dataset, fields


def test_canonical_value_contracts():
    assert canonicalize_record_value("string", "  Cafe\N{COMBINING ACUTE ACCENT}  ") == (
        "Café",
        b"sCaf\xc3\xa9",
    )
    assert canonicalize_record_value("number", "1.00") == (Decimal("1.00"), b"n1")
    assert canonicalize_record_value("number", "-0.000") == (Decimal(0), b"n0")
    assert canonicalize_record_value("boolean", "false") == (False, b"b0")
    assert canonicalize_record_value("date", "0001-01-01")[1] == b"d0001-01-01"
    assert canonicalize_record_value("date", "9999-12-31")[1] == b"d9999-12-31"
    assert canonicalize_record_value("string", "  ") is None


@pytest.mark.parametrize(
    ("data_type", "raw"),
    [
        ("string", "bad\nvalue"),
        ("string", "é" * 501),
        ("number", "+1"),
        ("number", "01"),
        ("number", "1e2"),
        ("number", "123456789012345678901"),
        ("number", "0.1234567890123456789"),
        ("boolean", "on"),
        ("date", "2024-02-30"),
        ("date", "2024-1-01"),
    ],
)
def test_invalid_canonical_values_are_rejected(data_type, raw):
    with pytest.raises(ValidationError):
        canonicalize_record_value(data_type, raw)


def test_create_normalized_record_values_identifier_and_audit(record_services):
    _, factory, services, admin = record_services
    dataset, fields = configured_dataset(services, admin)

    record = services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": " Café ", "amount": "1.00", "active": "false", "day": "2024-02-29"},
    )

    assert record.dataset_id == dataset.id
    with factory() as session:
        persisted = session.get(DatasetRecord, record.id)
        values = list(
            session.scalars(
                select(DatasetRecordValue).where(DatasetRecordValue.record_id == record.id)
            )
        )
        assert persisted.identifier_key.startswith(len(b"n1").to_bytes(4, "big") + b"n1")
        assert persisted.revision == 1
        assert len(values) == 4
        assert {value.field_id for value in values} == set(fields[field].id for field in fields)
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type == "dataset_record",
                    AuditEvent.object_id == record.id,
                )
            )
        )
        assert [event.action for event in events] == ["created"]


@pytest.mark.parametrize("raw", ["", "0", "01", "+1", "-1", " 1", "1 ", "1.0", "x"])
def test_expected_revision_rejects_noncanonical_values(raw):
    with pytest.raises(ValidationError):
        parse_expected_revision(raw)


def test_expected_revision_accepts_supported_positive_integer():
    assert parse_expected_revision("1") == 1
    assert parse_expected_revision("2147483647") == 2_147_483_647
    with pytest.raises(ValidationError, match="out of range"):
        parse_expected_revision("2147483648")


def test_update_replaces_all_values_preserves_uuid_and_detects_stale(record_services):
    _, factory, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": "one", "amount": "1", "active": "false", "day": "2024-01-01"},
    )

    updated = services.dataset_records.update_record(
        admin.id,
        dataset.key,
        record.id,
        1,
        {"code": "two", "amount": "2.00", "active": "true"},
    )
    assert updated.id == record.id
    assert updated.revision == 2
    assert {value.field_key: value.value for value in updated.values} == {
        "code": "two",
        "amount": Decimal("2.00"),
        "active": True,
    }

    with pytest.raises(StaleRecordError):
        services.dataset_records.update_record(
            admin.id,
            dataset.key,
            record.id,
            1,
            {"code": "stale", "amount": "3"},
        )
    with factory() as session:
        persisted = session.get(DatasetRecord, record.id)
        assert persisted.revision == 2
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.object_id == record.id).order_by(AuditEvent.id)
            )
        )
        assert sorted(event.action for event in events) == ["created", "updated"]


def test_update_uniqueness_self_reuse_and_rollback(record_services):
    _, factory, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    first = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "one", "amount": "1", "day": "2024-01-01"}
    )
    second = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "two", "amount": "2", "day": "2024-01-02"}
    )
    services.dataset_records.update_record(
        admin.id,
        dataset.key,
        first.id,
        1,
        {"code": "one", "amount": "1.0", "day": "2024-01-01"},
    )
    with pytest.raises(RecordConflictError, match="identifier"):
        services.dataset_records.update_record(
            admin.id,
            dataset.key,
            first.id,
            1,
            {"code": "two", "amount": "2", "day": "2024-01-01"},
        )
    with pytest.raises(RecordConflictError, match="unique"):
        services.dataset_records.update_record(
            admin.id,
            dataset.key,
            first.id,
            1,
            {"code": "changed", "amount": "3", "day": "2024-01-02"},
        )
    with factory() as session:
        persisted = session.get(DatasetRecord, first.id)
        assert persisted.revision == 1
    services.dataset_records.update_record(
        admin.id,
        dataset.key,
        second.id,
        1,
        {"code": "two", "amount": "2", "day": "2024-01-03"},
    )
    services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "three", "amount": "3", "day": "2024-01-02"}
    )


def test_delete_is_conditional_cascades_and_leaves_audit(record_services):
    _, factory, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "one", "amount": "1", "day": "2024-01-01"}
    )
    services.dataset_records.update_record(
        admin.id, dataset.key, record.id, 1, {"code": "one", "amount": "1"}
    )
    with pytest.raises(StaleRecordError):
        services.dataset_records.delete_record(admin.id, dataset.key, record.id, 1)
    services.dataset_records.delete_record(admin.id, dataset.key, record.id, 2)
    with pytest.raises(NotFoundError):
        services.dataset_records.delete_record(admin.id, dataset.key, record.id, 2)
    with factory() as session:
        assert session.get(DatasetRecord, record.id).deleted_content is not None
        assert (
            session.scalar(
                select(func.count(DatasetRecordValue.record_id)).where(
                    DatasetRecordValue.record_id == record.id
                )
            )
            == 0
        )
        actions = list(
            session.scalars(select(AuditEvent.action).where(AuditEvent.object_id == record.id))
        )
        assert sorted(actions) == ["created", "deleted", "updated"]


def test_audit_failure_rolls_back_record_update_and_delete(record_services, monkeypatch):
    _, factory, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "original", "amount": "1"}
    )
    original_add_audit = SqlAlchemyUnitOfWork.add_audit

    def invalid_audit(self, **values):
        values["actor_user_id"] = "missing-actor"
        original_add_audit(self, **values)

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "add_audit", invalid_audit)
    with pytest.raises(IntegrityError):
        services.dataset_records.update_record(
            admin.id,
            dataset.key,
            record.id,
            1,
            {"code": "changed", "amount": "2"},
        )
    with factory() as session:
        persisted = session.get(DatasetRecord, record.id)
        assert persisted.revision == 1
    unchanged = services.dataset_records.get_record_for_edit(admin.id, dataset.key, record.id)
    assert {value.field_key: value.value for value in unchanged.values} == {
        "code": "original",
        "amount": Decimal(1),
    }
    with pytest.raises(IntegrityError):
        services.dataset_records.delete_record(admin.id, dataset.key, record.id, 1)
    assert (
        services.dataset_records.get_record_for_edit(admin.id, dataset.key, record.id).revision == 1
    )


def test_required_unknown_duplicate_identifier_and_unique_value(record_services):
    _, _, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    with pytest.raises(RecordValidationError) as missing:
        services.dataset_records.create_record(admin.id, dataset.key, {"code": "one"})
    assert missing.value.errors == {"amount": "A value is required"}
    with pytest.raises(RecordValidationError) as unknown:
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "one", "amount": "1", "foreign": "x"}
        )
    assert unknown.value.errors == {"foreign": "Unknown Dataset field"}

    services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "one", "amount": "1.0", "day": "2024-01-01"}
    )
    with pytest.raises(RecordConflictError, match="identifier"):
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "one", "amount": "1.00"}
        )
    with pytest.raises(RecordConflictError, match="Day must be unique") as unique:
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "two", "amount": "2", "day": "2024-01-01"}
        )
    assert unique.value.field_key == "day"


def test_optional_absence_ordering_and_pagination(record_services):
    _, factory, services, admin = record_services
    dataset, fields = configured_dataset(services, admin)
    for index in range(52, 0, -1):
        services.dataset_records.create_record(
            admin.id,
            dataset.key,
            {"code": f"item-{index:02}", "amount": str(index)},
        )

    first = services.dataset_records.list_records(admin.id, dataset.key)
    second = services.dataset_records.list_records(admin.id, dataset.key, 2)
    beyond = services.dataset_records.list_records(admin.id, dataset.key, 999)
    assert (len(first.records), len(second.records), len(beyond.records)) == (50, 2, 0)
    first_amounts = [
        dict((value.field_key, value.value) for value in record.values)["amount"]
        for record in first.records[:3]
    ]
    assert first_amounts == [
        Decimal(1),
        Decimal(2),
        Decimal(3),
    ]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(DatasetRecordValue.record_id)).where(
                    DatasetRecordValue.field_id == fields["active"].id
                )
            )
            == 0
        )


def test_listing_tolerates_legacy_record_missing_identifier_value(record_services):
    _, factory, services, admin = record_services
    dataset, fields = configured_dataset(services, admin)
    complete = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "complete", "amount": "1"}
    )
    incomplete = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "legacy", "amount": "2"}
    )
    with factory.begin() as session:
        session.execute(
            DatasetRecordValue.__table__.delete().where(
                DatasetRecordValue.record_id == incomplete.id,
                DatasetRecordValue.field_id == fields["code"].id,
            )
        )

    page = services.dataset_records.list_records(admin.id, dataset.key)
    assert [record.id for record in page.records] == [complete.id, incomplete.id]


def test_incomplete_legacy_ordering_and_pagination_are_deterministic(record_services):
    _, factory, services, admin = record_services
    dataset, fields = configured_dataset(services, admin)
    records = [
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": f"item-{index:02}", "amount": str(index)}
        )
        for index in range(1, 56)
    ]
    incomplete = records[-4:]
    with factory.begin() as session:
        session.execute(
            DatasetRecordValue.__table__.delete().where(
                DatasetRecordValue.record_id == incomplete[0].id,
                DatasetRecordValue.field_id == fields["code"].id,
            )
        )
        session.execute(
            DatasetRecordValue.__table__.delete().where(
                DatasetRecordValue.record_id == incomplete[1].id,
                DatasetRecordValue.field_id == fields["amount"].id,
            )
        )
        session.execute(
            DatasetRecordValue.__table__.delete().where(
                DatasetRecordValue.record_id.in_([incomplete[2].id, incomplete[3].id]),
                DatasetRecordValue.field_id.in_([fields["code"].id, fields["amount"].id]),
            )
        )

    first = services.dataset_records.list_records(admin.id, dataset.key, 1)
    second = services.dataset_records.list_records(admin.id, dataset.key, 2)
    ordered_ids = [record.id for record in (*first.records, *second.records)]
    assert len(ordered_ids) == len(set(ordered_ids)) == 55
    assert set(ordered_ids[-4:]) == {record.id for record in incomplete}
    assert ordered_ids[-2:] == sorted((incomplete[2].id, incomplete[3].id))
    assert ordered_ids == [
        record.id
        for page in (1, 2)
        for record in services.dataset_records.list_records(admin.id, dataset.key, page).records
    ]


def test_record_aware_schema_guards_and_safe_changes(record_services):
    _, _, services, admin = record_services
    dataset, fields = configured_dataset(services, admin)
    empty = services.dataset_fields.create_field(admin.id, dataset.key, "empty", "Empty", "string")
    services.dataset_records.create_record(admin.id, dataset.key, {"code": "one", "amount": "1"})

    with pytest.raises(ValidationError, match="Required fields cannot be added"):
        services.dataset_fields.create_field(
            admin.id, dataset.key, "late", "Late", "string", is_required=True
        )
    with pytest.raises(ValidationError, match="identifier cannot change"):
        services.dataset_fields.update_field_and_business_key_membership(
            admin.id,
            dataset.key,
            fields["active"].key,
            "Active",
            "boolean",
            True,
            False,
            True,
        )

    with pytest.raises(ValidationError, match="type cannot change"):
        services.dataset_fields.update_field(
            admin.id, dataset.key, fields["amount"].key, "Amount", "date", True, False
        )
    with pytest.raises(ValidationError, match="Required cannot"):
        services.dataset_fields.update_field(
            admin.id, dataset.key, fields["active"].key, "Active", "boolean", True, False
        )
    with pytest.raises(ValidationError, match="identifier cannot change"):
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    changed = services.dataset_fields.update_field(
        admin.id, dataset.key, fields["code"].key, "Renamed", "string", True, False
    )
    assert changed.name == "Renamed"
    services.dataset_fields.delete_field(admin.id, dataset.key, empty.key)


def test_sqlite_concurrent_duplicate_identifier_creates_one(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'records.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset, _ = configured_dataset(services, admin)
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return services.dataset_records.create_record(
                admin.id, dataset.key, {"code": "same", "amount": "1"}
            )
        except RecordConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    assert sum(isinstance(result, RecordConflictError) for result in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count(DatasetRecord.id))) == 1


def test_browser_add_list_validation_security_and_escaping(record_services):
    _, _, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
    ).test_client()
    login_form = client.get("/login", base_url="https://localhost")
    csrf = login_form.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    response = client.post(
        "/login",
        base_url="https://localhost",
        data={"email": admin.email, "password": PASSWORD, "csrf_token": csrf},
    )
    assert response.status_code == 302
    form = client.get(f"/datasets/{dataset.key}/records/new", base_url="https://localhost")
    body = form.get_data(as_text=True)
    form_csrf = body.split("name='csrf_token' value='")[1].split("'", 1)[0]
    assert "Add record" in body

    invalid = client.post(
        f"/datasets/{dataset.key}/records",
        base_url="https://localhost",
        data={"code": "<script>x</script>", "amount": "bad", "csrf_token": form_csrf},
    )
    assert invalid.status_code == 400
    assert "&lt;script&gt;x&lt;/script&gt;" in invalid.get_data(as_text=True)
    assert "Enter a decimal number" in invalid.get_data(as_text=True)

    created = client.post(
        f"/datasets/{dataset.key}/records",
        base_url="https://localhost",
        data={"code": "<b>safe</b>", "amount": "2", "csrf_token": form_csrf},
    )
    assert created.status_code == 302
    listing = client.get(f"/datasets/{dataset.key}/concept/records", base_url="https://localhost")
    assert "&lt;b&gt;safe&lt;/b&gt;" in listing.get_data(as_text=True)
    assert '<svg class="key-icon"' in listing.get_data(as_text=True)
    assert (
        client.get(
            f"/datasets/{dataset.key}/concept/records?page=1&page=2", base_url="https://localhost"
        ).status_code
        == 400
    )


def test_viewer_lists_but_cannot_add(record_services):
    _, _, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    viewer = services.users.create_user(admin.id, "viewer@example.com", "Viewer", PASSWORD)
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    publish_dataset(services, admin.id, dataset)
    assert services.dataset_records.list_records(viewer.id, dataset.key).records == ()
    with pytest.raises(NotFoundError, match="Dataset not found"):
        services.dataset_records.create_record(
            viewer.id, dataset.key, {"code": "one", "amount": "1"}
        )


def test_dataset_permission_matrix_and_cross_dataset_record_scope(record_services):
    _, _, services, admin = record_services
    dataset, _ = configured_dataset(services, admin)
    other, _ = configured_dataset(services, admin, "other")
    record = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "one", "amount": "1"}
    )
    viewer = services.users.create_user(admin.id, "viewer2@example.com", "Viewer", PASSWORD)
    editor = services.users.create_user(admin.id, "editor@example.com", "Editor", PASSWORD)
    dataset_admin = services.users.create_user(
        admin.id, "dataset-admin@example.com", "Dataset admin", PASSWORD
    )
    services.grants.create_grant(admin.id, "user", viewer.id, "dataset", dataset.id, "viewer")
    services.grants.create_grant(admin.id, "user", editor.id, "dataset", dataset.id, "editor")
    services.grants.create_grant(admin.id, "user", dataset_admin.id, "dataset", dataset.id, "admin")
    services.memberships.create_membership(admin.id, editor.id, dataset.owner_team_id)
    services.memberships.create_membership(admin.id, dataset_admin.id, dataset.owner_team_id)
    for actor in (editor, dataset_admin, admin):
        assert services.dataset_records.get_record_for_edit(
            actor.id, dataset.key, record.id
        ).id == (record.id)
    for operation in (
        lambda: services.dataset_records.get_record_for_edit(viewer.id, dataset.key, record.id),
        lambda: services.dataset_records.update_record(
            viewer.id, dataset.key, record.id, 1, {"code": "x", "amount": "1"}
        ),
        lambda: services.dataset_records.delete_record(viewer.id, dataset.key, record.id, 1),
        lambda: services.dataset_records.get_record_for_edit(admin.id, other.key, record.id),
    ):
        with pytest.raises(NotFoundError):
            operation()


def test_browser_edit_delete_prepopulation_validation_and_escaping(record_services):
    _, factory, services, admin = record_services
    dataset, fields = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": "<code>", "amount": "1.20", "active": "false", "day": "2024-02-29"},
    )
    legacy_edit = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "legacy-edit", "amount": "8"}
    )
    legacy_delete = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "legacy-delete", "amount": "9"}
    )
    with factory.begin() as session:
        session.execute(
            DatasetRecordValue.__table__.delete().where(
                DatasetRecordValue.record_id.in_([legacy_edit.id, legacy_delete.id]),
                DatasetRecordValue.field_id == fields["code"].id,
            )
        )
    client = create_flask_app(
        services.users,
        services.authentication,
        services.sessions,
        services.teams,
        services.memberships,
        authorization=services.authorization,
        datasets=services.datasets,
        dataset_fields=services.dataset_fields,
        dataset_business_keys=services.dataset_business_keys,
        dataset_records=services.dataset_records,
    ).test_client()
    login = client.get("/login", base_url="https://localhost")
    csrf = login.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    client.post(
        "/login",
        base_url="https://localhost",
        data={"email": admin.email, "password": PASSWORD, "csrf_token": csrf},
    )

    listing = client.get(f"/datasets/{dataset.key}/concept/records", base_url="https://localhost")
    assert "Edit" in listing.get_data(as_text=True)
    assert listing.status_code == 200
    assert listing.get_data(as_text=True).count(">Added</span>") == 3
    assert "Incomplete record" not in listing.get_data(as_text=True)
    assert "slegacy" not in listing.get_data(as_text=True)
    edit_url = f"/datasets/{dataset.key}/records/{record.id}/edit"
    edit = client.get(edit_url, base_url="https://localhost")
    body = edit.get_data(as_text=True)
    assert "value='&lt;code&gt;'" in body
    assert "value='1.2'" in body
    assert "value='2024-02-29'" in body
    assert "value='false' selected" in body
    assert "name='expected_revision' value='1'" in body
    csrf = body.split("name='csrf_token' value='")[1].split("'", 1)[0]

    invalid = client.post(
        edit_url,
        base_url="https://localhost",
        data={
            "code": "<script>x</script>",
            "amount": "bad",
            "expected_revision": "1",
            "csrf_token": csrf,
        },
    )
    assert invalid.status_code == 400
    assert "&lt;script&gt;x&lt;/script&gt;" in invalid.get_data(as_text=True)
    assert (
        services.dataset_records.get_record_for_edit(admin.id, dataset.key, record.id).revision == 1
    )

    saved = client.post(
        edit_url,
        base_url="https://localhost",
        data={
            "code": "changed",
            "amount": "2",
            "active": "true",
            "day": "",
            "expected_revision": "1",
            "csrf_token": csrf,
        },
    )
    assert saved.status_code == 302
    stale = client.post(
        edit_url,
        base_url="https://localhost",
        data={
            "code": "stale",
            "amount": "3",
            "expected_revision": "1",
            "csrf_token": csrf,
        },
    )
    assert stale.status_code == 409

    delete_url = f"/datasets/{dataset.key}/records/{record.id}/delete"
    confirmation = client.get(delete_url, base_url="https://localhost")
    assert "2 / changed" in confirmation.get_data(as_text=True)
    assert "cannot be undone" in confirmation.get_data(as_text=True)
    assert (
        client.post(
            delete_url,
            base_url="https://localhost",
            data={"expected_revision": "2", "csrf_token": csrf},
        ).status_code
        == 400
    )
    deleted = client.post(
        delete_url,
        base_url="https://localhost",
        data={"confirm": "yes", "expected_revision": "2", "csrf_token": csrf},
    )
    assert deleted.status_code == 302
    assert client.get(delete_url, base_url="https://localhost").status_code == 404

    legacy_edit_url = f"/datasets/{dataset.key}/records/{legacy_edit.id}/edit"
    legacy_form = client.get(legacy_edit_url, base_url="https://localhost")
    assert legacy_form.status_code == 200
    assert "name='code'" in legacy_form.get_data(as_text=True)
    blocked = client.post(
        legacy_edit_url,
        base_url="https://localhost",
        data={"amount": "8", "expected_revision": "1", "csrf_token": csrf},
    )
    assert blocked.status_code == 400
    assert "A value is required" in blocked.get_data(as_text=True)
    repaired = client.post(
        legacy_edit_url,
        base_url="https://localhost",
        data={
            "code": "repaired",
            "amount": "8",
            "expected_revision": "1",
            "csrf_token": csrf,
        },
    )
    assert repaired.status_code == 302

    legacy_delete_url = f"/datasets/{dataset.key}/records/{legacy_delete.id}/delete"
    legacy_confirmation = client.get(legacy_delete_url, base_url="https://localhost")
    assert legacy_confirmation.status_code == 200
    assert "Record identifier: Incomplete record" in legacy_confirmation.get_data(as_text=True)
    assert (
        client.post(
            legacy_delete_url,
            base_url="https://localhost",
            data={"confirm": "yes", "expected_revision": "1", "csrf_token": csrf},
        ).status_code
        == 302
    )
    with pytest.raises(NotFoundError):
        services.dataset_records.get_record_for_edit(admin.id, dataset.key, legacy_delete.id)


@pytest.mark.postgres
def test_postgresql_constraints_order_and_atomicity(postgresql_record_services):
    _, factory, services, admin = postgresql_record_services
    dataset, _ = configured_dataset(services, admin)
    for code, amount in (("z", "10"), ("é", "2"), ("e", "1")):
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": code, "amount": amount}
        )
    page = services.dataset_records.list_records(admin.id, dataset.key)
    ordered = [{value.field_key: value.value for value in record.values} for record in page.records]
    assert [row["amount"] for row in ordered] == [Decimal(1), Decimal(2), Decimal(10)]
    with pytest.raises(RecordConflictError):
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "e", "amount": "1.00"}
        )
    with factory() as session:
        assert session.scalar(select(func.count(DatasetRecord.id))) == 3
        assert (
            session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.object_type == "dataset_record")
            )
            == 3
        )


@pytest.mark.postgres
def test_postgresql_same_revision_update_race_has_one_stale(postgresql_record_services):
    _, _, services, admin = postgresql_record_services
    dataset, _ = configured_dataset(services, admin)
    record = services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "original", "amount": "1"}
    )
    barrier = Barrier(2)

    def update_record(code):
        barrier.wait()
        try:
            return services.dataset_records.update_record(
                admin.id,
                dataset.key,
                record.id,
                1,
                {"code": code, "amount": "1"},
            )
        except StaleRecordError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update_record, ("first", "second")))
    assert sum(isinstance(result, StaleRecordError) for result in results) == 1
    assert (
        services.dataset_records.get_record_for_edit(admin.id, dataset.key, record.id).revision == 2
    )


def test_populated_ref_75_sqlite_upgrade_preserves_schema_and_adds_records(tmp_path):
    database = tmp_path / "populated-ref-75.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_database_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    services = create_community_services(factory, ResourceTypeRegistry())
    services.users.bootstrap_organisation()
    admin = services.users.create_initial_admin("admin@example.com", "Admin", PASSWORD)
    dataset = create_owned_dataset(services.datasets, admin.id, "preserved", "Preserved", "")
    downgrade_historical_fixture(config, "0007_ref_75")
    field_id = str(uuid.uuid4())
    now = utc_now()
    with factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO dataset_fields (id, dataset_id, key, name, data_type, is_required, "
                "is_unique, created_at, updated_at) VALUES (:id, :dataset_id, 'code', 'Code', "
                "'string', 1, 0, :now, :now)"
            ),
            {"id": field_id, "dataset_id": dataset.id, "now": now},
        )
        session.execute(
            text(
                "INSERT INTO dataset_business_key_fields (dataset_id, field_key) "
                "VALUES (:dataset_id, 'code')"
            ),
            {"dataset_id": dataset.id},
        )
    engine.dispose()

    command.upgrade(config, "head")
    upgraded = create_database_engine(f"sqlite:///{database}")
    upgraded_factory = create_session_factory(upgraded)
    upgraded_services = create_community_services(upgraded_factory, ResourceTypeRegistry())
    assert upgraded_services.dataset_business_keys.get_business_key(
        admin.id, dataset.key
    ).field_keys == ("code",)
    upgraded_services.dataset_versions.create_concept(admin.id, dataset.id)
    record = upgraded_services.dataset_records.create_record(admin.id, dataset.key, {"code": "one"})
    assert record.dataset_id == dataset.id
