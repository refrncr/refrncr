"""Disposable in-memory REF-84 browser fixture, loopback only."""

from test_dataset_browser import create_dataset, create_field, dataset_browser

from refrncr.models import utc_now
from refrncr.web import create_flask_app

_factory, s, admin, _client = dataset_browser.__wrapped__()
now = utc_now()
s.dataset_versions.clock = lambda: now
password = "correct horse battery staple"
owner = s.users.create_user(admin.id, "owner@example.com", "Dataset owner", password)
reader = s.users.create_user(admin.id, "reader@example.com", "Reader", password)
team = next(team for team in s.teams.list_teams(admin.id) if team.name == "Test data domain")
s.memberships.create_membership(admin.id, owner.id, team.id)
for width in (320, 768, 1440):
    key = f"diff-{width}"
    dataset = create_dataset(s, admin, key, f"Review demo {width}", "Published description")
    create_field(s, admin, dataset, "code", "Code", is_required=True)
    create_field(s, admin, dataset, "label", "Label")
    s.dataset_business_keys.set_business_key(admin.id, key, ("code",))
    changed = s.dataset_records.create_record(
        admin.id, key, {"code": "OLD", "label": "Before value"}
    )
    removed = s.dataset_records.create_record(
        admin.id, key, {"code": "REMOVED", "label": "Removed value"}
    )
    for index in range(51):
        create_field(s, admin, dataset, f"extra{index:02}", f"Extra {index:02}")
    version = s.dataset_versions.publish(
        admin.id, dataset.id, now.date(), "", s.datasets.get_dataset(admin.id, key).revision
    )
    s.dataset_versions.create_concept(admin.id, dataset.id)
    s.dataset_records.update_record(
        admin.id,
        key,
        changed.id,
        1,
        {"code": "NEW", "label": "<script>unsafe</script> " + "X" * 900},
    )
    s.dataset_records.delete_record(admin.id, key, removed.id, 1)
    for index in range(51):
        s.dataset_records.create_record(
            admin.id,
            key,
            {
                "code": f"ADDED{index:02}",
                **{f"extra{n:02}": "Long value " + "Y" * 900 for n in range(51)},
            },
        )
    s.datasets.update_dataset(
        admin.id, key, f"Review demo {width}", "<b>Concept description</b> " + "z" * 2000
    )
    s.dataset_fields.update_field(admin.id, key, "label", "Renamed label", "string", False, False)
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
    dataset_versions=s.dataset_versions,
    allow_loopback_http=True,
)
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5184, threaded=True, use_reloader=False)
