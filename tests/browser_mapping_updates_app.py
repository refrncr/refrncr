"""Disposable browser fixture for compatible and blocked source updates."""

from datetime import timedelta

from browser_mapping_app import admin, app, mapping, services
from dataset_helpers import create_mapping_dataset, publish_dataset


def publish_source(source):
    revision = services.datasets.get_dataset_for_edit(admin.id, source.key).revision
    services.dataset_versions.publish(
        admin.id,
        source.id,
        services.dataset_versions.clock.today() + timedelta(days=7),
        "",
        revision,
    )


publish_dataset(services, admin.id, mapping)
source = services.datasets.get_dataset_for_edit(admin.id, mapping.key + "-source")
services.dataset_versions.create_concept(admin.id, source.id)
for record in services.dataset_records.list_records(admin.id, source.key).records:
    if record.values[0].value == "EUR":
        services.dataset_records.delete_record(admin.id, source.key, record.id, record.revision)
services.dataset_records.create_record(admin.id, source.key, {"code": "GBP"})
publish_source(source)

blocked = create_mapping_dataset(
    services,
    admin,
    "blocked-mapping",
    "Blocked mapping",
    source_name="Blocked source",
    records=("A",),
)
services.dataset_fields.create_field(admin.id, blocked.key, "target", "Target", "string")
publish_dataset(services, admin.id, blocked)
source = services.datasets.get_dataset_for_edit(admin.id, blocked.key + "-source")
services.dataset_versions.create_concept(admin.id, source.id)
for record in services.dataset_records.list_records(admin.id, source.key).records:
    services.dataset_records.delete_record(admin.id, source.key, record.id, record.revision)
services.dataset_fields.create_field(
    admin.id, source.key, "new_code", "New code", "string", True, True
)
services.dataset_business_keys.set_business_key(admin.id, source.key, ("new_code",))
services.dataset_fields.update_field(admin.id, source.key, "code", "Code", "string", False, False)
publish_source(source)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5193, threaded=True, use_reloader=False)
