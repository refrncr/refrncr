"""Disposable REF-83 Chrome fixture, in-memory database and loopback binding only."""

from datetime import timedelta

from support import create_dataset, create_field, dataset_browser

from refrncr.models import utc_now
from refrncr.web import create_flask_app

_factory, s, admin, _client = dataset_browser.__wrapped__()
now = utc_now()
s.dataset_versions.clock = lambda: now
PASSWORD = "correct horse battery staple"
owner = s.users.create_user(admin.id, "owner@example.com", "Dataset owner", PASSWORD)
reader = s.users.create_user(admin.id, "reader@example.com", "Reader", PASSWORD)
exporter = s.users.create_user(admin.id, "exporter@example.com", "Exporter", PASSWORD)
team = next(team for team in s.teams.list_teams(admin.id) if team.name == "Test data domain")
s.memberships.create_membership(admin.id, owner.id, team.id)


def source(key, name):
    dataset = create_dataset(s, admin, key, name, "Published description")
    create_field(s, admin, dataset, "code", "Code", is_required=True)
    create_field(s, admin, dataset, "label", "Label")
    s.dataset_business_keys.set_business_key(admin.id, key, ("code",))
    record = s.dataset_records.create_record(
        admin.id, key, {"code": "FIRST", "label": "Published value"}
    )
    return dataset, record


def publish(dataset, effective=None, note=""):
    current = s.datasets.get_dataset(admin.id, dataset.key)
    return s.dataset_versions.publish(
        admin.id, dataset.id, effective or s.dataset_versions.clock().date(), note, current.revision
    )


for width in (320, 768, 1440):
    source(f"publish-{width}", f"Publication {width}")
source("publish-nojs", "Publication without JavaScript")
history, record = source("history", "History demo")
s.dataset_versions.clock = lambda: now - timedelta(days=2)
publish(history, note="First published version")
s.dataset_versions.clock = lambda: now - timedelta(days=1)
s.dataset_versions.create_concept(admin.id, history.id)
s.dataset_records.update_record(
    admin.id, history.key, record.id, 1, {"code": "FIRST", "label": "Second published value"}
)
publish(history, note="Current version")
s.dataset_versions.clock = lambda: now
s.dataset_versions.create_concept(admin.id, history.id)
s.datasets.update_dataset(admin.id, history.key, "Unpublished history name", "DRAFT DESCRIPTION")
s.dataset_records.update_record(
    admin.id, history.key, record.id, 2, {"code": "FIRST", "label": "DRAFT ONLY"}
)
s.datasets.grant_export_access(admin.id, history.key, "user", exporter.id)
future, _ = source("future-only", "Private future")
publish(future, now.date() + timedelta(days=1), "Private Scheduled release")
app = create_flask_app(
    s.users,
    s.authentication,
    s.sessions,
    s.teams,
    s.memberships,
    authorization=s.authorization,
    datasets=s.datasets,
    dataset_fields=s.dataset_fields,
    dataset_business_keys=s.dataset_business_keys,
    dataset_records=s.dataset_records,
    record_imports=s.record_imports,
    dataset_versions=s.dataset_versions,
    allow_loopback_http=True,
)
app.jinja_env.auto_reload = True
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5183, threaded=True, use_reloader=False)
