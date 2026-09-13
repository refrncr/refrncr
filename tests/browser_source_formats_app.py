"""Disposable REF-154 browser fixture. Never points at an application database."""

from dataset_helpers import create_owned_dataset
from test_dataset_browser import dataset_browser

from refrncr.web import create_flask_app

factory, services, admin, _client = dataset_browser.__wrapped__()
dataset = create_owned_dataset(services.datasets, admin.id, "imports", "Imports", "")
for key, datatype, required in (
    ("code", "string", True),
    ("amount", "number", True),
    ("day", "date", False),
    ("stamp", "timestamp", False),
    ("moment", "datetime", False),
    ("clock", "time", False),
    ("measure", "float", False),
):
    services.dataset_fields.create_field(
        admin.id, dataset.key, key, key.title(), datatype, required, False
    )
services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
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
    allow_loopback_http=True,
)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5154, threaded=True, use_reloader=False)
