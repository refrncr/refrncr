"""Disposable REF-160 fixture for real-browser verification."""

from browser_concept_app import admin, app, now, services
from support import create_dataset, create_field

for width in (320, 768, 1440, "nojs", "empty"):
    dataset = create_dataset(services, admin, f"new-version-{width}", f"New version {width}")
    create_field(services, admin, dataset, "code", "Code", is_required=True)
    create_field(services, admin, dataset, "label", "Label")
    services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
    if width == "empty":
        continue
    for code in ("changed", "deleted", "unchanged"):
        services.dataset_records.create_record(
            admin.id, dataset.key, {"code": code, "label": "Old value"}
        )
    services.dataset_versions.publish(
        admin.id,
        dataset.id,
        now.date(),
        "Baseline",
        services.datasets.get_dataset_for_edit(admin.id, dataset.key).revision,
    )
    services.dataset_versions.create_concept(admin.id, dataset.id)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5187, threaded=True, use_reloader=False)
