"""REF-155 disposable browser data; start separately from the lifecycle fixture."""

from browser_concept_app import admin, app, create_dataset, create_field, now, services

for width in (320, 375, 767, 768, 1440):
    for kind in ("unchanged", "mixed", "first", "fallback"):
        dataset = create_dataset(
            services,
            admin,
            f"discard-{kind}-{width}",
            f"Reference classifications with a long Dataset name {kind} {width}",
            "Published description",
        )
        create_field(services, admin, dataset, "code", "Code", is_required=True)
        create_field(services, admin, dataset, "label", "Label")
        services.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
        original = services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "Original", "label": "Published value"}
        )
        removed = services.dataset_records.create_record(
            admin.id, dataset.key, {"code": "Retained", "label": "Retained value"}
        )
        if kind == "first":
            continue
        services.dataset_versions.publish(
            admin.id,
            dataset.id,
            now.date(),
            "First publication",
            services.datasets.get_dataset_for_edit(admin.id, dataset.key).revision,
        )
        services.dataset_versions.create_concept(admin.id, dataset.id)
        if kind == "mixed":
            services.dataset_fields.create_field(admin.id, dataset.key, "extra", "Extra", "string")
            services.dataset_records.update_record(
                admin.id,
                dataset.key,
                original.id,
                1,
                {"code": "Original", "label": "Unpublished value"},
            )
            services.dataset_records.delete_record(admin.id, dataset.key, removed.id, 1)
            services.dataset_records.create_record(admin.id, dataset.key, {"code": "Added"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5186, threaded=True, use_reloader=False)
