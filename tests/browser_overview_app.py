"""Disposable Overview fixture; run with an existing Playwright/Chrome installation."""

from datetime import UTC, datetime, timedelta

from browser_mapping_updates_app import admin, app, mapping, services
from dataset_helpers import create_mapping_dataset, publish_dataset
from support import MutableClock

from refrncr.models import DatasetConcept
from refrncr.overview import OverviewService

# Include both a coming source + stale Draft and a source update already prepared.
services.dataset_versions.create_concept(admin.id, mapping.id)
clock = MutableClock(datetime.now(UTC))
services.datasets.uow.clock = services.dataset_versions.clock = clock
reporting = create_mapping_dataset(
    services,
    admin,
    "reporting",
    "Cost centre reporting mapping",
    source_name="Cost centres",
    records=("100", "200"),
)
services.dataset_fields.create_field(admin.id, reporting.key, "target", "Target", "string")
publish_dataset(services, admin.id, reporting)
source = services.datasets.get_dataset_for_edit(admin.id, reporting.key + "-source")
services.dataset_versions.create_concept(admin.id, source.id)
services.dataset_records.create_record(admin.id, source.key, {"code": "300"})
clock.advance(days=1)
services.dataset_versions.publish(
    admin.id,
    source.id,
    clock.today(),
    "",
    services.datasets.get_dataset_for_edit(admin.id, source.key).revision,
)
services.dataset_versions.update_mapping_source(
    admin.id,
    reporting.id,
    services.datasets.get_dataset_for_edit(admin.id, reporting.key).revision,
)

with services.datasets.uow(write=True) as work:
    for dataset in work.datasets.list_all():
        dataset.description = (
            "Maps local codes to the shared reporting standard."
            if dataset.kind == "mapping"
            else "Shared reference values used across the organisation."
        )
        concept = work.session.get(DatasetConcept, dataset.id)
        if concept:
            concept.updated_at = work.now() - timedelta(days=21)

visits = OverviewService(services.datasets.uow, services.authorization.datasets)
with services.datasets.uow() as work:
    datasets = work.datasets.list_all()
for dataset in datasets:
    visits.record_visit(admin.id, key=dataset.key)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5195, threaded=True, use_reloader=False)
