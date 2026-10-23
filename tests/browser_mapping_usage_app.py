"""Disposable source-side mapping usage fixture, including private drafts."""

from datetime import timedelta

from browser_mapping_app import admin, app, mapping, services
from dataset_helpers import publish_dataset
from support import MutableClock

publish_dataset(services, admin.id, mapping)
source = services.datasets.get_dataset_for_edit(admin.id, mapping.key + "-source")
services.dataset_versions.create_concept(admin.id, source.id)
services.dataset_records.create_record(admin.id, source.key, {"code": "GBP"})
revision = services.datasets.get_dataset_for_edit(admin.id, source.key).revision
services.dataset_versions.publish(
    admin.id, source.id, services.dataset_versions.clock.today() + timedelta(days=7), "", revision
)
services.dataset_versions.update_mapping_source(
    admin.id, mapping.id, services.datasets.get_dataset_for_edit(admin.id, mapping.key).revision
)
finance = services.teams.create_team(admin.id, "Finance")
services.datasets.create_dataset(
    admin.id,
    "finance-mapping",
    "Finance currency mapping",
    "",
    finance.id,
    kind="mapping",
    source_dataset_id=source.id,
    source_field_key="code",
)
services.users.create_user(admin.id, "reader@example.com", "Reader", "correct horse battery staple")
services.dataset_versions.clock = MutableClock(
    services.dataset_versions.clock.now() + timedelta(days=7)
)
services.dataset_versions.create_concept(admin.id, source.id)
services.dataset_records.create_record(admin.id, source.key, {"code": "JPY"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5194, threaded=True, use_reloader=False)
