"""Pinned mapping source contracts on SQLite and PostgreSQL."""

from datetime import timedelta

import pytest
from support import NOW, setup

from refrncr.services import ValidationError


def create_mapping(c, key="mapping"):
    return c.s.datasets.create_dataset(
        c.a.id,
        key,
        "Mapping",
        "",
        c.owner.id,
        kind="mapping",
        source_dataset_id=c.d.id,
        source_field_key="code",
    )


def publish_table(c, dataset, day=None):
    revision = c.s.datasets.get_dataset_for_edit(c.a.id, dataset.key).revision
    return c.s.dataset_versions.publish(
        c.a.id, dataset.id, day or c.s.dataset_versions.clock.today(), "", revision
    )


def prepare(c):
    setup(c)
    c.s.dataset_versions.clock = c.service.clock
    return c


def test_mapping_requires_current_source(database_context):
    c = prepare(database_context)
    with pytest.raises(ValidationError, match="Current"):
        create_mapping(c)
    scheduled = publish_table(c, c.d, NOW.date() + timedelta(days=1))
    with pytest.raises(ValidationError, match="Current"):
        create_mapping(c)
    c.service.clock.set(NOW + timedelta(days=1))
    mapping = create_mapping(c)
    assert mapping.source_version_id == scheduled.id


def test_mapping_cannot_publish_before_source_effective_date(database_context):
    c = prepare(database_context)
    source = publish_table(c, c.d)
    mapping = create_mapping(c)
    c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target", "string")
    revision = c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).revision
    with pytest.raises(ValidationError):
        c.s.dataset_versions.publish(
            c.a.id, mapping.id, source.effective_date - timedelta(days=1), "", revision
        )
    assert c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).source_version_id == source.id


@pytest.mark.parametrize("missing", ["source_dataset_id", "source_field_key"])
def test_mapping_source_and_identifier_required(database_context, missing):
    c = prepare(database_context)
    publish_table(c, c.d)
    args = {"source_dataset_id": c.d.id, "source_field_key": "code"}
    args.pop(missing)
    with pytest.raises(ValidationError):
        c.s.datasets.create_dataset(
            c.a.id, "mapping", "Mapping", "", c.owner.id, kind="mapping", **args
        )


def test_source_snapshot_survives_drafts_publications_and_clock_changes(database_context):
    c = prepare(database_context)
    first = publish_table(c, c.d)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.datasets.update_dataset(c.a.id, c.d.key, "Source draft name", "")
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, c.record.revision, {"code": "a", "label": "Source draft"}
    )
    mapping = create_mapping(c)
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["name"] == first.name
    c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target code", "string")
    context = c.s.dataset_fields.create_field(
        c.a.id,
        mapping.key,
        "ignored",
        "Ignored",
        "string",
        mapping_role="source_context",
        source_field_key="label",
    )
    fields = c.s.dataset_fields.list_fields(c.a.id, mapping.key)
    assert [f.key for f in fields] == ["code", "label", "target"]
    row = c.s.dataset_records.list_records(c.a.id, mapping.key).records[0]
    assert {v.field_key: v.value for v in row.values} == {"code": "a", "label": "Café"}
    c.s.dataset_records.update_record(
        c.a.id,
        mapping.key,
        row.id,
        row.revision,
        {"code": "forged", "label": "forged", "target": "mapped"},
    )
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["latest_number"] is None
    mapping_v1 = publish_table(c, mapping)
    assert mapping_v1.source_version_id == first.id
    second = publish_table(c, c.d, NOW.date() + timedelta(days=1))
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["latest_number"] == 2
    assert create_mapping(c, "another").source_version_id == first.id
    c.service.clock.set(NOW + timedelta(days=1))
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    assert c.s.datasets.get_dataset_for_edit(c.a.id, mapping.key).source_version_id == first.id
    row = c.s.dataset_records.list_records(c.a.id, mapping.key).records[0]
    assert {v.field_key: v.value for v in row.values} == {
        "code": "a",
        "label": "Café",
        "target": "mapped",
    }
    c.s.dataset_records.update_record(
        c.a.id, mapping.key, row.id, row.revision, {"target": "updated"}
    )
    mapping_v2 = publish_table(c, mapping)
    assert mapping_v2.source_version_id == first.id
    assert create_mapping(c, "new-current").source_version_id == second.id
    c.s.dataset_versions.create_concept(c.a.id, mapping.id)
    with pytest.raises(ValidationError, match="read-only"):
        c.s.dataset_fields.update_field(
            c.a.id, mapping.key, context.key, "Changed", "string", False, False
        )


def test_cancelled_source_does_not_trigger_update(database_context):
    c = prepare(database_context)
    publish_table(c, c.d)
    mapping = create_mapping(c)
    c.s.dataset_versions.create_concept(c.a.id, c.d.id)
    c.s.dataset_records.update_record(
        c.a.id, c.d.key, c.record.id, 1, {"code": "a", "label": "New"}
    )
    version = publish_table(c, c.d, NOW.date() + timedelta(days=1))
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["latest_number"] == 2
    c.s.dataset_versions.cancel(c.a.id, c.d.id, version.id)
    assert c.s.datasets.mapping_source(c.a.id, mapping.key)["latest_number"] is None


def test_source_values_cannot_be_forged_or_deleted(database_context):
    c = prepare(database_context)
    c.s.dataset_fields.create_field(c.a.id, c.d.key, "optional", "Optional", "string")
    publish_table(c, c.d)
    mapping = create_mapping(c)
    c.s.dataset_fields.create_field(
        c.a.id,
        mapping.key,
        "optional",
        "Optional",
        "string",
        mapping_role="source_context",
        source_field_key="optional",
    )
    c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target", "string")
    row = c.s.dataset_records.list_records(c.a.id, mapping.key).records[0]
    result = c.s.dataset_records.update_record(
        c.a.id,
        mapping.key,
        row.id,
        row.revision,
        {"code": "changed", "optional": "forged", "target": "Allowed"},
    )
    assert {v.field_key: v.value for v in result.values} == {"code": "a", "target": "Allowed"}
    with pytest.raises(ValidationError, match="read-only"):
        c.s.dataset_records.delete_record(c.a.id, mapping.key, row.id, result.revision)


def test_mapping_source_details_follow_mapping_version_authority(database_context):
    from refrncr.services import NotFoundError

    c = prepare(database_context)
    publish_table(c, c.d)
    mapping = create_mapping(c)
    with pytest.raises(NotFoundError):
        c.s.datasets.mapping_source(c.u.id, mapping.key)
    c.s.dataset_fields.create_field(c.a.id, mapping.key, "target", "Target", "string")
    scheduled = publish_table(c, mapping, NOW.date() + timedelta(days=1))
    with pytest.raises(NotFoundError):
        c.s.datasets.mapping_source(c.u.id, mapping.key, scheduled.id)
    c.service.clock.set(NOW + timedelta(days=1))
    assert c.s.datasets.mapping_source(c.u.id, mapping.key, scheduled.id)["number"] == 1
