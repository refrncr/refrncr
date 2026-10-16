"""Disposable in-memory fixture for the six REF-165 final-review regressions."""

import logging

from browser_table_rows_app import _factory, admin, app, create_dataset, create_field, s
from dataset_helpers import publish_dataset

from refrncr.models import Dataset

for width in (320, 768, 1440):
    dataset = create_dataset(s, admin, f"ref165-{width}", f"Review {width}")
    create_field(s, admin, dataset, "code", "Code", is_required=True)
    create_field(s, admin, dataset, "label", "Label")
    s.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    s.dataset_records.create_record(admin.id, dataset.key, {"code": "A", "label": "Alpha"})
    publish_dataset(s, admin.id, dataset)
    s.dataset_versions.create_concept(admin.id, dataset.id)
    # Legacy database state, before the new catalog was installed.
    with _factory.begin() as session:
        session.get(Dataset, dataset.id).icon = "history"

if __name__ == "__main__":
    # Avoid filling a subprocess pipe with static-asset request logs during acceptance.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=5165, threaded=True, use_reloader=False)
