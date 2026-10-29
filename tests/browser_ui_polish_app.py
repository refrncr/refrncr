"""Disposable REF-162 browser fixture; in-memory data only."""

from support import create_dataset, dataset_browser

from refrncr.web import create_flask_app

_factory, s, admin, _client = dataset_browser.__wrapped__()
password = "correct horse battery staple"
reader = s.users.create_user(admin.id, "reader@example.com", "Reader", password)
for i in range(4):
    user = s.users.create_user(admin.id, f"member{i}@example.com", f"Member {i}", password)
    team = s.teams.create_team(admin.id, f"Domain {i}")
    s.memberships.create_membership(admin.id, user.id, team.id)
    s.memberships.create_membership(admin.id, admin.id, team.id)
for width in (320, 375, 767, 768, 1440):
    d = create_dataset(s, admin, f"polish-{width}", f"Polish {width}")
    s.dataset_fields.create_field(
        admin.id,
        d.key,
        "e_number",
        "E_NUMBER",
        "string",
        True,
        True,
        True,
        definition="A readable identifier",
        unit="days",
    )
    for key, name in (
        ("in_eu_database", "IN_EU_DATABASE"),
        ("in_wikipedia", "IN_WIKIPEDIA"),
        ("eu_status", "EU_STATUS"),
        ("eu_status_addition", "EU_STATUS_ADDITION"),
        ("us_status", "US_STATUS"),
    ):
        s.dataset_fields.create_field(admin.id, d.key, key, name, "string", False, False, False)
    for record_number in range(24):
        s.dataset_records.create_record(
            admin.id,
            d.key,
            {
                "e_number": f"A{record_number}",
                "in_eu_database": "yes",
                "in_wikipedia": "yes",
                "eu_status": "active",
                "eu_status_addition": "",
                "us_status": "active",
            },
        )
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
    app.run(host="127.0.0.1", port=5162, threaded=True, use_reloader=False)
