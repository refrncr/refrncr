"""Disposable app with an export-only actor for export-menu acceptance tests."""

from browser_table_rows_app import admin, app, d, hierarchy, mapping, s

exporter_only = s.users.create_user(
    admin.id,
    "exporter-only@example.com",
    "Exporter only",
    "correct horse battery staple",
)
for dataset in (d, hierarchy, mapping):
    s.datasets.grant_export_access(admin.id, dataset.key, "user", exporter_only.id)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5144, threaded=True, use_reloader=False)
