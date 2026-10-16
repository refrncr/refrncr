"""Disposable REF-145 loopback browser fixture; no application data."""

from support import dataset_browser, setup_dataset

from refrncr.web import create_flask_app

factory, services, admin, _client = dataset_browser.__wrapped__()
setup_dataset(services, admin, ())
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
    allow_loopback_http=True,
)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5146, threaded=True, use_reloader=False)
