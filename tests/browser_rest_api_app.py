"""Disposable REF-107 ASGI fixture; no real accounts, keys or data."""

import uvicorn
from browser_api_keys_app import admin, keys, services

from refrncr.app import create_application
from refrncr.clock import SYSTEM_CLOCK

team = services.teams.create_team(admin.id, "Synthetic domain")
d = services.datasets.create_dataset(
    admin.id, "countries", "Countries", "Synthetic browser fixture", team.id
)
services.dataset_fields.create_field(admin.id, d.key, "code", "Code", "string", True, True, True)
services.dataset_records.create_record(admin.id, d.key, {"code": "NL"})
services.dataset_versions.publish(
    admin.id,
    d.id,
    SYSTEM_CLOCK.today(),
    "",
    services.datasets.get_dataset_for_edit(admin.id, d.key).revision,
)
app = create_application(
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
    api_keys=keys,
    allow_loopback_http=True,
)
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5147, access_log=False)
