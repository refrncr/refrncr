"""Disposable pinned-mapping fixture for browser and accessibility checks."""

from dataset_helpers import create_mapping_dataset
from support import _dataset_browser

from refrncr.web import create_flask_app

_factory, services, admin, _client = _dataset_browser()
mapping = create_mapping_dataset(
    services,
    admin,
    "mapping-browser",
    "Currency mapping",
    source_name="Currencies",
    records=("EUR", "USD"),
)
for key in ("target", "label"):
    services.dataset_fields.create_field(admin.id, mapping.key, key, key.title(), "string")
for record in services.dataset_records.list_records(admin.id, mapping.key).records:
    code = record.values[0].value
    services.dataset_records.update_record(
        admin.id,
        mapping.key,
        record.id,
        record.revision,
        {
            "target": code + "-mapped",
            "label": "A long target label available in full to keyboard and screen reader users",
        },
    )
app = create_flask_app(
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
    record_imports=services.record_imports,
    dataset_versions=services.dataset_versions,
    allow_loopback_http=True,
)
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5193, threaded=True, use_reloader=False)
