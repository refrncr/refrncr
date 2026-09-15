"""Disposable REF-136 fixture; no developer data or access-log query values."""

import logging

from test_dataset_browser import create_dataset, create_field, dataset_browser

from refrncr.models import utc_now
from refrncr.web import create_flask_app

_sessions, services, admin, _client = dataset_browser.__wrapped__()
now = utc_now()
services.dataset_versions.clock = lambda: now
for suffix in ("320", "768", "1440", "nojs"):
    dataset = create_dataset(services, admin, f"search-{suffix}", f"Record search {suffix}")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "label", "Label")
    for key, datatype in (
        ("amount", "number"),
        ("count", "integer"),
        ("day", "date"),
        ("enabled", "boolean"),
    ):
        create_field(services, admin, dataset, key, key.title(), datatype)
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    records = [
        services.dataset_records.create_record(
            admin.id,
            dataset.key,
            {
                "code": f"id-{index:03}",
                "label": "Amsterdam" if index < 60 else "Rotterdam",
                "count": str(index),
                "amount": "17.500",
                "day": "2026-09-15",
                "enabled": "true",
            },
        )
        for index in range(65)
    ]
    services.dataset_versions.publish(
        admin.id,
        dataset.id,
        now.date(),
        "Baseline",
        services.datasets.get_dataset_for_edit(admin.id, dataset.key).revision,
    )
    services.dataset_versions.create_concept(admin.id, dataset.id)
    services.dataset_records.delete_record(admin.id, dataset.key, records[59].id, 1)
    services.dataset_records.create_record(
        admin.id, dataset.key, {"code": "new", "label": "Concept only"}
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
    dataset_versions=services.dataset_versions,
    record_imports=services.record_imports,
    allow_loopback_http=True,
)
app.jinja_env.auto_reload = True
logging.getLogger("werkzeug").disabled = True
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5136, threaded=True, use_reloader=False)
