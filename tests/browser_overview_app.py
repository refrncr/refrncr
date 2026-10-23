"""Disposable Overview fixture; run with an existing Playwright/Chrome installation."""

from datetime import timedelta

from browser_mapping_updates_app import admin, app, services

from refrncr.models import DatasetConcept
from refrncr.overview import OverviewService

with services.datasets.uow(write=True) as work:
    for dataset in work.datasets.list_all():
        concept = work.session.get(DatasetConcept, dataset.id)
        if concept:
            concept.updated_at = work.now() - timedelta(days=21)

visits = OverviewService(services.datasets.uow, services.authorization.datasets)
with services.datasets.uow() as work:
    datasets = work.datasets.list_all()
for dataset in datasets:
    visits.record_visit(admin.id, key=dataset.key)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5195, threaded=True, use_reloader=False)
