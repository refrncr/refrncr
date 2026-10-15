"""Disposable fixture for report sizing and mobile Users browser regressions."""

from browser_table_rows_app import admin, app, s
from support import create_dataset, create_field

for width in (320, 768, 1440):
    for count in (1, 100):
        dataset = create_dataset(s, admin, f"report-{width}-{count}", "Report sizing")
        create_field(s, admin, dataset, "code", "Code", is_required=True)
        create_field(s, admin, dataset, "description", "Description")
        s.dataset_business_keys.set_business_key(admin.id, dataset.key, ("code",))
        s.dataset_records.create_record(admin.id, dataset.key, {"code": "DUPLICATE"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5186, threaded=True, use_reloader=False)
