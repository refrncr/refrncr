"""Disposable in-memory app for the optional REF-144 browser regression script."""

from dataset_helpers import publish_dataset
from test_dataset_browser import create_dataset, create_field, dataset_browser

from refrncr.web import create_flask_app

_factory, s, admin, _ = dataset_browser.__wrapped__()
d = create_dataset(s, admin, "row-check", "Row check")
create_field(s, admin, d, "description", "Description")
create_field(s, admin, d, "code", "Code", is_required=True)
s.dataset_business_keys.set_business_key(admin.id, d.key, ("code",))
for i in range(52):
    s.dataset_records.create_record(
        admin.id,
        d.key,
        {
            "code": f"ITEM-{i:02}",
            "description": "Selectable row text " + ("Long" * 100 if i == 0 else str(i)),
        },
    )
publish_dataset(s, admin.id, d)
create_dataset(s, admin, "empty-check", "Empty check")
create_dataset(s, admin, "long-check", "Long" * 18 + " <img src=x onerror=alert(1)>")
s.teams.create_team(admin.id, "Row team")
s.teams.create_team(admin.id, "Long" * 18 + " <script>alert(1)</script>")
s.users.create_user(admin.id, "row@example.com", "Row user", "correct horse battery staple")
s.users.create_user(
    admin.id,
    "long@example.com",
    "Long" * 18 + " <img src=x onerror=alert(1)>",
    "correct horse battery staple",
)
# REF-73 acceptance actors: distinct membership states, without Dataset Grants.
for label, team_names in (
    ("single", ("Test data domain",)),
    ("multiple", ("Test data domain", "Row team")),
):
    actor = s.users.create_user(
        admin.id, f"{label}@example.com", label.title(), "correct horse battery staple"
    )
    for team in s.teams.list_teams(admin.id):
        if team.name in team_names:
            s.memberships.create_membership(admin.id, actor.id, team.id)
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
    allow_loopback_http=True,
)
app.jinja_env.auto_reload = True
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5144, threaded=True, use_reloader=False)
