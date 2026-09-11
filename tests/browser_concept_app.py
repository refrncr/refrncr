"""Disposable explicit-concept browser fixture; no persistent user data."""

from test_dataset_browser import create_dataset, create_field, dataset_browser

from refrncr.models import utc_now
from refrncr.web import create_flask_app

_factory, services, admin, _client = dataset_browser.__wrapped__()
now = utc_now()
services.dataset_versions.clock = lambda: now
password = "correct horse battery staple"
owner = services.users.create_user(admin.id, "owner@example.com", "Dataset owner", password)
reader = services.users.create_user(admin.id, "reader@example.com", "Reader", password)
team = next(t for t in services.teams.list_teams(admin.id) if t.name == "Test data domain")
services.memberships.create_membership(admin.id, owner.id, team.id)
for width in (320, 768, 1440):
    dataset = create_dataset(
        services,
        admin,
        f"lifecycle-{width}",
        f"Payments and detailed reference classifications {width}",
        "Published description",
    )
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "status", "Status")
    create_field(services, admin, dataset, "label", "Label")
    create_field(services, admin, dataset, "change", "Change")
    create_field(services, admin, dataset, "validation", "Validation")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    services.dataset_records.create_record(
        admin.id,
        dataset.key,
        {"code": "Betaling", "status": "Complete", "label": "Published value"},
    )
    for code in ("Gepubliceerd", "Verwijderen"):
        services.dataset_records.create_record(
            admin.id,
            dataset.key,
            {
                "code": code,
                "status": "Complete",
                "label": "Snapshot value",
                "change": "Content change",
                "validation": "Content validation",
            },
        )
    services.dataset_versions.publish(
        admin.id,
        dataset.id,
        now.date(),
        "First publication",
        services.datasets.get_dataset_for_edit(admin.id, dataset.key).revision,
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
app.jinja_env.auto_reload = True
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5185, threaded=True, use_reloader=False)
